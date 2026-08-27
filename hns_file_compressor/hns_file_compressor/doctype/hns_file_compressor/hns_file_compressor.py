# Copyright (c) 2026, Hns File Compressor and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import os
import io
from PIL import Image
import zipfile
try:
	from PyPDF2 import PdfMerger
except ImportError:
	from pypdf import PdfWriter as PdfMerger

from hns_file_compressor.utils import (
	save_frappe_file,
	download_google_drive_image,
	upload_to_gdrive,
	compress_pdf_bytes,
	compress_image_bytes
)

class HNSFileCompressor(Document):
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
		original_data = download_google_drive_image(row.attachment)
		if not original_data:
			frappe.msgprint(f"Failed to download image from Google Drive: {row.attachment}")
			return
		
		original_size = len(original_data)
		row.original_file_size = f"{original_size / 1024:.2f} KB"
		
		if row.file_type == "Pdf":
			compressed_data, ext = compress_pdf_bytes(original_data, row.compressor_factor)
			new_file_name = f"compressed_{frappe.generate_hash(length=8)}.pdf"
			mimetype = "application/pdf"
		else:
			compressed_data, ext = compress_image_bytes(original_data, row.compressor_factor)
			new_file_name = f"compressed_{frappe.generate_hash(length=8)}.jpg"
			mimetype = "image/jpeg"

		folder = self.folder_name or "Compressed"
		if getattr(self, "upload_google_drive", 0):
			google_drive_url, error = upload_to_gdrive(new_file_name, compressed_data, folder, mimetype=mimetype)
			if google_drive_url:
				row.compressed_file = google_drive_url
			else:
				frappe.msgprint(f"Failed to upload compressed image to Google Drive: {error}")
				row.compressed_file = save_frappe_file(new_file_name, compressed_data, self.doctype, self.name, 0)
		else:
			row.compressed_file = save_frappe_file(new_file_name, compressed_data, self.doctype, self.name, 0)
			
		row.compressed_file_size = f"{len(compressed_data) / 1024:.2f} KB"
		row.is_compressed = 1

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
			compressed_data, ext = compress_pdf_bytes(original_data, row.compressor_factor)
			file_name = f"compressed_{file_doc.file_name}"
			if not file_name.lower().endswith(".pdf"):
				file_name += ".pdf"
			mimetype = "application/pdf"
		else:
			compressed_data, img_format = compress_image_bytes(original_data, row.compressor_factor)
			file_name = f"compressed_{file_doc.file_name}"
			if not file_name.lower().endswith(".jpg") and img_format == "JPEG":
				file_name += ".jpg"
			mimetype = f"image/{img_format.lower()}"
			
		folder = self.folder_name or "Compressed"
		if getattr(self, "upload_google_drive", 0):
			google_drive_url, error = upload_to_gdrive(file_name, compressed_data, folder, mimetype=mimetype)
			if google_drive_url:
				row.compressed_file = google_drive_url
			else:
				frappe.msgprint(f"Failed to upload compressed file to Google Drive: {error}")
				row.compressed_file = save_frappe_file(file_name, compressed_data, self.doctype, self.name, file_doc.is_private)
		else:
			row.compressed_file = save_frappe_file(file_name, compressed_data, self.doctype, self.name, file_doc.is_private)
			
		row.compressed_file_size = f"{len(compressed_data) / 1024:.2f} KB"
		row.is_compressed = 1

	def combine_files(self):
		if not self.hns_file_list:
			return

		files_to_combine = []
		for row in self.hns_file_list:
			file_url = row.compressed_file if row.compressed_file else row.attachment
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
		
		folder = self.folder_name or "Compressed"
		if getattr(self, "upload_google_drive", 0):
			google_drive_url, error = upload_to_gdrive(file_name, pdf_data, folder, mimetype="application/pdf")
			if google_drive_url:
				self.combine_file = google_drive_url
			else:
				self.combine_file = save_frappe_file(file_name, pdf_data, self.doctype, self.name, 0)
		else:
			self.combine_file = save_frappe_file(file_name, pdf_data, self.doctype, self.name, 0)

	def create_combined_zip(self, files):
		zip_buffer = io.BytesIO()
		with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
			for file_name, file_data, file_url in files:
				zip_file.writestr(file_name, file_data)
				
		zip_data = zip_buffer.getvalue()
		
		file_name = self.file_name or f"Combined_{self.name}"
		if not file_name.lower().endswith('.zip'):
			file_name += ".zip"
		
		folder = self.folder_name or "Compressed"
		if getattr(self, "upload_google_drive", 0):
			google_drive_url, error = upload_to_gdrive(file_name, zip_data, folder, mimetype="application/zip")
			if google_drive_url:
				self.combine_file = google_drive_url
			else:
				self.combine_file = save_frappe_file(file_name, zip_data, self.doctype, self.name, 0)
		else:
			self.combine_file = save_frappe_file(file_name, zip_data, self.doctype, self.name, 0)
