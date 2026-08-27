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
			if not (row.attachment and row.compressor_factor):
				continue
				
			if row.is_compressed:
				upload_gd = int(row.upload_google_drive or 0)
				is_on_gdrive = "drive.google.com" in str(row.compressed_file or "")
				if upload_gd and not is_on_gdrive:
					self.upload_existing_local_to_gdrive(row)
				elif not upload_gd and is_on_gdrive:
					self.save_existing_gdrive_to_local(row)
				continue
				
			if row.file_type in ["Image", "Pdf"]:
				self.process_file(row)
				
		if self.is_file_combine:
			self.combine_files()

	def _get_file_data(self, file_url):
		if not file_url: return None
		if "drive.google.com" in file_url:
			return download_google_drive_image(file_url)
			
		file_path = frappe.get_doc("File", {"file_url": file_url}).get_full_path()
		if os.path.exists(file_path):
			with open(file_path, "rb") as f:
				return f.read()
		return None

	def _get_original_filename(self, file_url):
		if not file_url or "drive.google.com" in file_url:
			return f"file_{frappe.generate_hash(length=8)}"
		try:
			return frappe.get_value("File", {"file_url": file_url}, "file_name") or f"file_{frappe.generate_hash(length=8)}"
		except Exception:
			return f"file_{frappe.generate_hash(length=8)}"
			
	def _get_original_is_private(self, file_url):
		if not file_url or "drive.google.com" in file_url:
			return 0
		return frappe.get_value("File", {"file_url": file_url}, "is_private") or 0

	def process_file(self, row):
		original_data = self._get_file_data(row.attachment)
		if not original_data:
			return frappe.msgprint(f"Failed to fetch original file: {row.attachment}")
		
		row.original_file_size = f"{len(original_data) / 1024:.2f} KB"
		orig_name = self._get_original_filename(row.attachment)
		
		if row.file_type == "Pdf":
			compressed_data, _ = compress_pdf_bytes(original_data, row.compressor_factor)
			file_name = f"compressed_{orig_name}" if orig_name.endswith('.pdf') else f"compressed_{orig_name}.pdf"
			mimetype = "application/pdf"
		else:
			compressed_data, img_format = compress_image_bytes(original_data, row.compressor_factor)
			file_name = f"compressed_{orig_name}"
			if not file_name.lower().endswith(".jpg") and img_format == "JPEG":
				file_name += ".jpg"
			mimetype = f"image/{img_format.lower()}"

		folder = row.folder_name or self.folder_name or "Compressed"
		is_private = self._get_original_is_private(row.attachment)
		
		self._save_file_to_destination(row, file_name, compressed_data, folder, mimetype, is_private)
		
		row.compressed_file_size = f"{len(compressed_data) / 1024:.2f} KB"
		row.is_compressed = 1

	def _save_file_to_destination(self, row, file_name, data, folder, mimetype, is_private):
		if int(row.upload_google_drive or 0):
			url, error = upload_to_gdrive(file_name, data, folder, mimetype=mimetype)
			if url:
				row.compressed_file = url
				return
			frappe.msgprint(f"Failed to upload to Google Drive: {error}")
			
		row.compressed_file = save_frappe_file(file_name, data, self.doctype, self.name, is_private)

	def save_existing_gdrive_to_local(self, row):
		try:
			data = download_google_drive_image(row.compressed_file)
			if not data:
				return frappe.msgprint("Failed to download file from Google Drive to save locally.")
				
			file_name = f"compressed_{self._get_original_filename(row.attachment)}"
			if row.file_type == "Pdf" and not file_name.lower().endswith(".pdf"): file_name += ".pdf"
			elif row.file_type == "Image" and not file_name.lower().endswith(".jpg"): file_name += ".jpg"
				
			is_private = self._get_original_is_private(row.attachment)
			row.compressed_file = save_frappe_file(file_name, data, self.doctype, self.name, is_private)
		except Exception as e:
			frappe.msgprint(f"Error saving Google Drive file to local: {str(e)}")

	def upload_existing_local_to_gdrive(self, row):
		try:
			data = self._get_file_data(row.compressed_file)
			if not data: return
				
			folder = row.folder_name or self.folder_name or "Compressed"
			mimetype = "application/pdf" if row.file_type == "Pdf" else "image/jpeg"
			file_name = self._get_original_filename(row.compressed_file)
				
			url, error = upload_to_gdrive(file_name, data, folder, mimetype=mimetype)
			if url:
				row.compressed_file = url
			else:
				frappe.msgprint(f"Failed to upload existing compressed file to Google Drive: {error}")
		except Exception as e:
			frappe.msgprint(f"Error uploading to Google Drive: {str(e)}")

	def combine_files(self):
		if not self.hns_file_list: return
		
		files_to_combine = []
		for row in self.hns_file_list:
			file_url = row.compressed_file or row.attachment
			if not file_url: continue
			
			data = self._get_file_data(file_url)
			if data:
				name = self._get_original_filename(file_url)
				if "drive.google.com" in file_url:
					name = f"{name}.pdf" if row.file_type == "Pdf" else f"{name}.jpg"
				files_to_combine.append((name, data, file_url))

		if not files_to_combine: return

		file_name = self.file_name or f"Combined_{self.name}"
		folder = self.folder_name or "Compressed"
		
		if self.combine_type == "Pdf":
			if not file_name.lower().endswith('.pdf'): file_name += ".pdf"
			combined_data, mimetype = self._create_combined_pdf(files_to_combine), "application/pdf"
		elif self.combine_type == "Zip":
			if not file_name.lower().endswith('.zip'): file_name += ".zip"
			combined_data, mimetype = self._create_combined_zip(files_to_combine), "application/zip"
		else:
			return
			
		if self.upload_google_drive:
			url, error = upload_to_gdrive(file_name, combined_data, folder, mimetype=mimetype)
			self.combine_file = url if url else save_frappe_file(file_name, combined_data, self.doctype, self.name, 0)
		else:
			self.combine_file = save_frappe_file(file_name, combined_data, self.doctype, self.name, 0)

	def _create_combined_pdf(self, files):
		merger = PdfMerger()
		for name, data, _ in files:
			if name.lower().endswith('.pdf'):
				merger.append(io.BytesIO(data))
			else:
				try:
					img = Image.open(io.BytesIO(data)).convert("RGB")
					img_pdf = io.BytesIO()
					img.save(img_pdf, format="PDF")
					img_pdf.seek(0)
					merger.append(img_pdf)
				except Exception as e:
					frappe.log_error(title="Combine PDF Error", message=str(e))
					
		out = io.BytesIO()
		merger.write(out)
		merger.close()
		return out.getvalue()

	def _create_combined_zip(self, files):
		out = io.BytesIO()
		with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
			for name, data, _ in files:
				z.writestr(name, data)
		return out.getvalue()
