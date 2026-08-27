import frappe
import os
import io
from PIL import Image
import requests
import re
import traceback

def save_frappe_file(file_name, content, attached_to_doctype=None, attached_to_name=None, is_private=0):
	if attached_to_doctype and attached_to_name:
		existing_file = frappe.db.get_value("File", {
			"attached_to_doctype": attached_to_doctype,
			"attached_to_name": attached_to_name,
			"file_name": file_name
		}, "name")
		
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
		"attached_to_doctype": attached_to_doctype,
		"attached_to_name": attached_to_name,
		"content": content,
		"is_private": is_private
	})
	new_file.insert(ignore_permissions=True)
	return new_file.file_url

def get_google_drive_file_id(url):
	match = re.search(r'(/d/|id=)([a-zA-Z0-9-_]+)', url)
	return match.group(2) if match else None

def download_google_drive_image(url):
	file_id = get_google_drive_file_id(url)
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

def upload_to_gdrive(file_name, file_content, folder_name="Compressed", mimetype='image/jpeg'):
	try:
		from frappe.integrations.doctype.google_drive.google_drive import get_google_drive_object
		from googleapiclient.http import MediaIoBaseUpload
		
		google_drive, account = get_google_drive_object()
		
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

def compress_pdf_bytes(original_data, target_size_ratio):
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

def compress_image_bytes(original_data, target_size_ratio):
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
