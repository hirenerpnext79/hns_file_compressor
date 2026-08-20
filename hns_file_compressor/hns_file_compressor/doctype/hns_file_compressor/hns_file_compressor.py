# Copyright (c) 2026, Hns File Compressor and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import os
import io
from PIL import Image
import requests
import re

class HNSFileCompressor(Document):
	def get_google_drive_file_id(self, url):
		match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
		if match:
			return match.group(1)
		match = re.search(r'id=([a-zA-Z0-9-_]+)', url)
		if match:
			return match.group(1)
		return None

	def download_google_drive_image(self, url):
		file_id = self.get_google_drive_file_id(url)
		if not file_id:
			return None
		
		download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
		response = requests.get(download_url)
		if response.status_code == 200:
			return response.content
		return None

	def upload_to_google_drive(self, file_name, file_content):
		try:
			from frappe.integrations.doctype.google_drive.google_drive import get_google_drive_object
			from googleapiclient.http import MediaIoBaseUpload
			import io
			
			google_drive, account = get_google_drive_object()
			
			# Find or create "compressed folder"
			folder_name = "compressed folder"
			query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
			results = google_drive.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
			folders = results.get('files', [])
			
			if folders:
				folder_id = folders[0].get('id')
			else:
				folder_metadata = {
					'name': folder_name,
					'mimeType': 'application/vnd.google-apps.folder'
				}
				folder = google_drive.files().create(body=folder_metadata, fields='id').execute()
				folder_id = folder.get('id')
			
			file_metadata = {
				"name": file_name,
				"parents": [folder_id]
			}
			
			media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype='image/jpeg', resumable=True)
			file = google_drive.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()
			
			return file.get("webViewLink")
		except Exception as e:
			frappe.log_error(f"Google Drive Upload Error: {str(e)}", "HNS File Compressor")
			return None

	def before_save(self):
		for row in self.hns_file_list:
			print("row.attachment", row.attachment)
			print("row.compressor_factor", row.compressor_factor)
			if row.attachment and row.compressor_factor:
				if row.comprassed_file:
					continue
					
				if "drive.google.com" in row.attachment:
					if row.file_type == "Image":
						original_data = self.download_google_drive_image(row.attachment)
						if not original_data:
							frappe.msgprint(f"Failed to download image from Google Drive: {row.attachment}")
							continue
						
						original_size = len(original_data)
						row.original_file_size = f"{original_size / 1024:.2f} KB"
						
						target_size = original_size * (1 - (row.compressor_factor / 100.0))
						
						img = Image.open(io.BytesIO(original_data))
						img_format = img.format if img.format else "JPEG"
						
						if img.mode != "RGB":
							img = img.convert("RGB")
							img_format = "JPEG"
						
						quality = 95
						compressed_data = None
						
						while quality > 5:
							buffer = io.BytesIO()
							img.save(buffer, format=img_format, quality=quality)
							size = buffer.tell()
							if size <= target_size:
								compressed_data = buffer.getvalue()
								break
							quality -= 5
							
						if not compressed_data:
							buffer = io.BytesIO()
							img.save(buffer, format=img_format, quality=5)
							compressed_data = buffer.getvalue()

						new_file_name = f"compressed_{frappe.generate_hash(length=8)}.jpg"
						google_drive_url = self.upload_to_google_drive(new_file_name, compressed_data)
						
						if google_drive_url:
							row.comprassed_file = google_drive_url
							row.comprassed_file_size = f"{len(compressed_data) / 1024:.2f} KB"
						else:
							frappe.msgprint(f"Failed to upload compressed image to Google Drive.")
				else:
					# Fetch original file info
					file_doc = frappe.get_doc("File", {"file_url": row.attachment})
					original_path = file_doc.get_full_path()
					
					# Set original file size in KB
					original_size = os.path.getsize(original_path)
					print("original_size", original_size)
					row.original_file_size = f"{original_size / 1024:.2f} KB"
					print("row.original_file_size", row.original_file_size)
						
					if row.file_type == "Image":
						# Target Size
						target_size = original_size * (1 - (row.compressor_factor / 100.0))
						
						img = Image.open(original_path)
						img_format = img.format if img.format else "JPEG"
						
						if img.mode != "RGB":
							img = img.convert("RGB")
							img_format = "JPEG"
						
						quality = 95
						compressed_data = None
						
						while quality > 5:
							buffer = io.BytesIO()
							img.save(buffer, format=img_format, quality=quality)
							size = buffer.tell()
							if size <= target_size:
								compressed_data = buffer.getvalue()
								break
							quality -= 5
							
						if not compressed_data:
							# If we can't reach the target size, just save at lowest quality
							buffer = io.BytesIO()
							img.save(buffer, format=img_format, quality=5)
							compressed_data = buffer.getvalue()
						
						# Save new file
						file_name = f"compressed_{file_doc.file_name}"
						if not file_name.endswith(".jpg") and img_format == "JPEG":
							file_name += ".jpg"
							
						new_file = frappe.get_doc({
							"doctype": "File",
							"file_name": file_name,
							"attached_to_doctype": self.doctype,
							"attached_to_name": self.name,
							"content": compressed_data,
							"is_private": file_doc.is_private
						})
						new_file.insert()
						
						row.comprassed_file = new_file.file_url
						
						new_size = len(compressed_data)
						row.comprassed_file_size = f"{new_size / 1024:.2f} KB"
