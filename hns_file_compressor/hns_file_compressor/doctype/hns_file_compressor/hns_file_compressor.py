# Copyright (c) 2026, Hns File Compressor and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import os
import io
from PIL import Image
import requests
import re
import traceback

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
			
			google_drive, account = get_google_drive_object()
			
			folder_name = "Compressed"
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
			
			return file.get("webViewLink"), None
		except Exception as e:
			error_msg = str(e)
			frappe.log_error(f"Google Drive Upload Error: {error_msg}\n{traceback.format_exc()}", "HNS File Compressor")
			return None, error_msg

	def compress_image_data(self, original_data, target_size_ratio):
		"""Compresses image bytes until it reaches the target size ratio, returns compressed bytes"""
		img = Image.open(io.BytesIO(original_data))
		img_format = img.format if img.format else "JPEG"
		
		if img.mode != "RGB":
			img = img.convert("RGB")
			img_format = "JPEG"
		
		original_size = len(original_data)
		target_size = original_size * (1 - (target_size_ratio / 100.0))
		
		quality = 95
		compressed_data = None
		
		while quality > 5:
			buffer = io.BytesIO()
			img.save(buffer, format=img_format, quality=quality)
			if buffer.tell() <= target_size:
				compressed_data = buffer.getvalue()
				break
			quality -= 5
			
		if not compressed_data:
			buffer = io.BytesIO()
			img.save(buffer, format=img_format, quality=5)
			compressed_data = buffer.getvalue()
			
		return compressed_data, img_format

	def before_save(self):
		for row in self.hns_file_list:
			if not (row.attachment and row.compressor_factor) or row.comprassed_file:
				continue
				
			if row.file_type != "Image":
				continue

			if "drive.google.com" in row.attachment:
				self.process_google_drive_file(row)
			else:
				self.process_local_file(row)

	def process_google_drive_file(self, row):
		original_data = self.download_google_drive_image(row.attachment)
		if not original_data:
			frappe.msgprint(f"Failed to download image from Google Drive: {row.attachment}")
			return
		
		original_size = len(original_data)
		row.original_file_size = f"{original_size / 1024:.2f} KB"
		
		compressed_data, img_format = self.compress_image_data(original_data, row.compressor_factor)

		new_file_name = f"compressed_{frappe.generate_hash(length=8)}.jpg"
		google_drive_url, error = self.upload_to_google_drive(new_file_name, compressed_data)
		
		if google_drive_url:
			row.comprassed_file = google_drive_url
			row.comprassed_file_size = f"{len(compressed_data) / 1024:.2f} KB"
		else:
			frappe.msgprint(f"Failed to upload compressed image to Google Drive: {error}")

	def process_local_file(self, row):
		file_doc = frappe.get_doc("File", {"file_url": row.attachment})
		original_path = file_doc.get_full_path()
		
		if not os.path.exists(original_path):
			frappe.msgprint(f"Local file not found: {original_path}")
			return

		with open(original_path, "rb") as f:
			original_data = f.read()

		original_size = len(original_data)
		row.original_file_size = f"{original_size / 1024:.2f} KB"
			
		compressed_data, img_format = self.compress_image_data(original_data, row.compressor_factor)
		
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
		row.comprassed_file_size = f"{len(compressed_data) / 1024:.2f} KB"
