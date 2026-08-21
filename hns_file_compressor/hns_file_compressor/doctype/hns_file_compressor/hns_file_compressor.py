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
import zipfile
try:
	from PyPDF2 import PdfMerger
except ImportError:
	from pypdf import PdfWriter as PdfMerger

class HNSFileCompressor(Document):
	def save_file(self, file_name, content, is_private=0):
		existing_file = frappe.db.get_value("File", {"attached_to_doctype": self.doctype, "attached_to_name": self.name, "file_name": file_name}, "name")
		if existing_file:
			old_doc = frappe.get_doc("File", existing_file)
			old_path = old_doc.get_full_path()
			frappe.delete_doc("File", existing_file, ignore_permissions=True)
			if os.path.exists(old_path):
				try:
					os.remove(old_path)
				except Exception:
					pass
			
		new_file = frappe.get_doc({
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": self.doctype,
			"attached_to_name": self.name,
			"content": content,
			"is_private": is_private
		})
		new_file.insert(ignore_permissions=True)
		return new_file.file_url

	def get_google_drive_file_id(self, url):
		match = re.search(r'(/d/|id=)([a-zA-Z0-9-_]+)', url)
		return match.group(2) if match else None

	def download_google_drive_image(self, url):
		file_id = self.get_google_drive_file_id(url)
		if not file_id:
			return None
		
		try:
			from frappe.integrations.doctype.google_drive.google_drive import get_google_drive_object
			from googleapiclient.http import MediaIoBaseDownload
			google_drive, account = get_google_drive_object()
			
			request = google_drive.files().get_media(fileId=file_id)
			fh = io.BytesIO()
			downloader = MediaIoBaseDownload(fh, request)
			done = False
			while not done:
				status, done = downloader.next_chunk()
			return fh.getvalue()
		except Exception as e:
			frappe.log_error(title="Google Drive Download API Failed, falling back", message=str(e))
			
		download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
		response = requests.get(download_url)
		if response.status_code == 200 and not response.headers.get('Content-Type', '').startswith('text/html'):
			return response.content
		return None

	def upload_to_google_drive(self, file_name, file_content, folder_name=None, mimetype='image/jpeg'):
		try:
			from frappe.integrations.doctype.google_drive.google_drive import get_google_drive_object
			from googleapiclient.http import MediaIoBaseUpload
			
			google_drive, account = get_google_drive_object()
			
			folder_name = folder_name or self.folder_name or "Compressed"
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
			
			media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype=mimetype, resumable=True)
			file = google_drive.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()
			
			return file.get("webViewLink"), None
		except Exception as e:
			error_msg = str(e)
			frappe.log_error(f"Google Drive Upload Error: {error_msg}\n{traceback.format_exc()}", "HNS File Compressor")
			return None, error_msg

	def compress_pdf_data(self, original_data, target_size_ratio):
		import subprocess
		import tempfile
		
		with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as input_pdf:
			input_pdf.write(original_data)
			input_path = input_pdf.name
			
		output_path = input_path + "_compressed.pdf"
		
		presets = ['/prepress', '/printer', '/ebook', '/screen']
		
		original_size = len(original_data)
		target_size = original_size * (1 - (target_size_ratio / 100.0))
		compressed_data = None
		
		try:
			for preset in presets:
				command = [
					"gs",
					"-sDEVICE=pdfwrite",
					"-dCompatibilityLevel=1.4",
					f"-dPDFSETTINGS={preset}",
					"-dNOPAUSE",
					"-dQUIET",
					"-dBATCH",
					f"-sOutputFile={output_path}",
					input_path
				]
				
				subprocess.run(command, check=True)
				
				if os.path.exists(output_path):
					with open(output_path, "rb") as f:
						compressed_data = f.read()
					if len(compressed_data) <= target_size:
						break
		except Exception as e:
			frappe.log_error(title="PDF Compression Error", message=str(e))
			compressed_data = original_data
		finally:
			if os.path.exists(input_path):
				os.remove(input_path)
			if os.path.exists(output_path):
				os.remove(output_path)
				
		return compressed_data if compressed_data else original_data, "PDF"

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
			if not (row.attachment and row.compressor_factor) or row.is_compressed:
				continue
				
			if row.file_type not in ["Image", "Pdf"]:
				continue

			if "drive.google.com" in row.attachment:
				self.process_google_drive_file(row)
			else:
				self.process_local_file(row)
				
		if self.is_file_combine:
			self.combine_files()

	def process_google_drive_file(self, row):
		original_data = self.download_google_drive_image(row.attachment)
		if not original_data:
			frappe.msgprint(f"Failed to download image from Google Drive: {row.attachment}")
			return
		
		original_size = len(original_data)
		row.original_file_size = f"{original_size / 1024:.2f} KB"
		
		if row.file_type == "Pdf":
			compressed_data, ext = self.compress_pdf_data(original_data, row.compressor_factor)
			new_file_name = f"compressed_{frappe.generate_hash(length=8)}.pdf"
			mimetype = "application/pdf"
		else:
			compressed_data, ext = self.compress_image_data(original_data, row.compressor_factor)
			new_file_name = f"compressed_{frappe.generate_hash(length=8)}.jpg"
			mimetype = "image/jpeg"

		google_drive_url, error = self.upload_to_google_drive(new_file_name, compressed_data, mimetype=mimetype)
		
		if google_drive_url:
			row.comprassed_file = google_drive_url
			row.comprassed_file_size = f"{len(compressed_data) / 1024:.2f} KB"
			row.is_compressed = 1
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
			
		if row.file_type == "Pdf":
			compressed_data, ext = self.compress_pdf_data(original_data, row.compressor_factor)
			file_name = f"compressed_{file_doc.file_name}"
			if not file_name.lower().endswith(".pdf"):
				file_name += ".pdf"
		else:
			compressed_data, img_format = self.compress_image_data(original_data, row.compressor_factor)
			file_name = f"compressed_{file_doc.file_name}"
			if not file_name.lower().endswith(".jpg") and img_format == "JPEG":
				file_name += ".jpg"
			
		row.comprassed_file = self.save_file(file_name, compressed_data, file_doc.is_private)
		row.comprassed_file_size = f"{len(compressed_data) / 1024:.2f} KB"
		row.is_compressed = 1

	def combine_files(self):
		if not self.hns_file_list:
			return

		files_to_combine = []
		for row in self.hns_file_list:
			file_url = row.comprassed_file if row.comprassed_file else row.attachment
			if not file_url:
				continue
			
			if "drive.google.com" in file_url:
				continue
			else:
				file_doc = frappe.get_doc("File", {"file_url": file_url})
				file_path = file_doc.get_full_path()
				if os.path.exists(file_path):
					with open(file_path, "rb") as f:
						files_to_combine.append((file_doc.file_name, f.read(), file_url))

		if not files_to_combine:
			return

		if self.combine_type == "Pdf":
			self.create_combined_pdf(files_to_combine)
		elif self.combine_type == "Zip":
			self.create_combined_zip(files_to_combine)

	def create_combined_pdf(self, files):
		merger = PdfMerger()
		
		for file_name, file_data, file_url in files:
			if file_name.lower().endswith('.pdf'):
				merger.append(io.BytesIO(file_data))
			else:
				try:
					img = Image.open(io.BytesIO(file_data))
					if img.mode != "RGB":
						img = img.convert("RGB")
					img_pdf = io.BytesIO()
					img.save(img_pdf, format="PDF")
					img_pdf.seek(0)
					merger.append(img_pdf)
				except Exception as e:
					frappe.log_error(title="Combine PDF Error", message=str(e))
					
		pdf_bytes = io.BytesIO()
		merger.write(pdf_bytes)
		merger.close()
		pdf_data = pdf_bytes.getvalue()
		
		file_name = self.file_name or f"Combined_{self.name}"
		if not file_name.lower().endswith('.pdf'):
			file_name += ".pdf"
		
		self.combine_file = self.save_file(file_name, pdf_data, 0)

	def create_combined_zip(self, files):
		zip_buffer = io.BytesIO()
		with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
			for file_name, file_data, file_url in files:
				zip_file.writestr(file_name, file_data)
				
		zip_data = zip_buffer.getvalue()
		
		file_name = self.file_name or f"Combined_{self.name}"
		if not file_name.lower().endswith('.zip'):
			file_name += ".zip"
		
		self.combine_file = self.save_file(file_name, zip_data, 0)