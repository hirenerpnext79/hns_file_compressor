import frappe
import os

from hns_file_compressor.utils import (
	save_frappe_file,
	download_google_drive_image,
	upload_to_gdrive,
	compress_pdf_bytes,
	compress_image_bytes
)

@frappe.whitelist(allow_guest=False)
def api_compress_file(file_url, file_type, compressor_factor=10, upload_google_drive=0, folder_name="Compressed"):
	"""
	API to compress a file given its URL and type (Image or Pdf).
	"""
	compressor_factor = frappe.utils.cint(compressor_factor)
	upload_google_drive = frappe.utils.cint(upload_google_drive)

	original_data = None
	original_file_name = "file"
	is_private = 0

	# 1. Download file data
	if "drive.google.com" in file_url:
		original_data = download_google_drive_image(file_url)
		original_file_name = f"google_drive_file_{frappe.generate_hash(length=8)}"
	else:
		try:
			file_doc = frappe.get_doc("File", {"file_url": file_url})
			original_path = file_doc.get_full_path()
			original_file_name = file_doc.file_name
			is_private = file_doc.is_private
			if os.path.exists(original_path):
				with open(original_path, "rb") as f:
					original_data = f.read()
		except Exception:
			pass

	if not original_data:
		frappe.throw(f"Could not read file from {file_url}")

	original_size = len(original_data)
	
	# 2. Compress data
	if file_type == "Pdf":
		compressed_data, ext = compress_pdf_bytes(original_data, compressor_factor)
		new_file_name = f"compressed_{original_file_name}"
		if not new_file_name.lower().endswith(".pdf"):
			new_file_name += ".pdf"
		mimetype = "application/pdf"
	else:
		compressed_data, img_format = compress_image_bytes(original_data, compressor_factor)
		new_file_name = f"compressed_{original_file_name}"
		if not new_file_name.lower().endswith(".jpg") and img_format == "JPEG":
			new_file_name += ".jpg"
		mimetype = f"image/{img_format.lower()}"

	compressed_size = len(compressed_data)
	final_url = None

	# 3. Save / Upload
	if upload_google_drive:
		google_drive_url, error = upload_to_gdrive(new_file_name, compressed_data, folder_name, mimetype=mimetype)
		if google_drive_url:
			final_url = google_drive_url
		else:
			frappe.msgprint(f"Failed to upload to Google Drive: {error}. Saving locally.")

	if not final_url:
		final_url = save_frappe_file(new_file_name, compressed_data, is_private=is_private)

	return {
		"success": True,
		"original_size": f"{original_size / 1024:.2f} KB",
		"compressed_size": f"{compressed_size / 1024:.2f} KB",
		"compressed_file_url": final_url,
		"file_name": new_file_name
	}
