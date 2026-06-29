import os
import tempfile
import fitz
from PIL import Image


class PDFMergerCore:
    def __init__(self):
        self.temp_files = []

    def cleanup_temp_files(self):
        for temp_file in self.temp_files:
            try:
                os.unlink(temp_file)
            except Exception:
                pass
        self.temp_files = []

    def convert_image_to_pdf(self, image_path):
        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_pdf.close()
        self.temp_files.append(temp_pdf.name)

        with Image.open(image_path) as img:
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            img.save(temp_pdf.name, 'PDF', resolution=300.0)

        return temp_pdf.name

    def process_pdf_page(self, file_path):
        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_pdf.close()
        self.temp_files.append(temp_pdf.name)

        doc = fitz.open(file_path)
        if doc.page_count > 0:
            writer = fitz.open()
            writer.insert_pdf(doc, from_page=0, to_page=0)
            writer.save(temp_pdf.name)
            writer.close()
            doc.close()
            return temp_pdf.name
        else:
            doc.close()
            raise Exception('PDF文件为空')

    def merge_files(self, files, rows=3, cols=2, orientation='纵向', progress_callback=None):
        if not files:
            raise Exception('没有可处理的文件')

        processed_files = []
        total_files = len(files)

        for i, file in enumerate(files):
            if file.lower().endswith('.pdf'):
                temp_pdf = self.process_pdf_page(file)
            else:
                temp_pdf = self.convert_image_to_pdf(file)
            processed_files.append(temp_pdf)

            if progress_callback:
                progress_value = int(((i + 1) / total_files) * 50)
                progress_callback(progress_value, f'正在处理文件: {i + 1}/{total_files}')

        output_doc = fitz.open()

        page_size = fitz.paper_size("a4")
        if orientation == '横向':
            page_size = (page_size[1], page_size[0])

        page_width, page_height = page_size
        files_per_page = rows * cols
        cell_width = page_width / cols
        cell_height = page_height / rows

        for i in range(0, len(processed_files), files_per_page):
            page_files = processed_files[i:i + files_per_page]

            new_page = output_doc.new_page(width=page_width, height=page_height)

            for j, pdf_file in enumerate(page_files):
                try:
                    row = j // cols
                    col = j % cols
                    x = col * cell_width
                    y = page_height - (row + 1) * cell_height

                    src_doc = fitz.open(pdf_file)
                    if src_doc.page_count > 0:
                        src_page = src_doc[0]
                        src_rect = src_page.rect
                        src_width = src_rect.width
                        src_height = src_rect.height

                        scale_x = (cell_width * 0.95) / src_width
                        scale_y = (cell_height * 0.95) / src_height
                        scale = min(scale_x, scale_y)

                        scaled_width = src_width * scale
                        scaled_height = src_height * scale
                        centered_x = x + (cell_width - scaled_width) / 2
                        centered_y = y + (cell_height - scaled_height) / 2

                        new_page.show_pdf_page(
                            fitz.Rect(centered_x, centered_y, centered_x + scaled_width, centered_y + scaled_height),
                            src_doc,
                            0
                        )
                    src_doc.close()

                    if progress_callback:
                        current_file_index = i + j
                        progress_value = int(50 + ((current_file_index + 1) / len(processed_files)) * 50)
                        progress_callback(progress_value, f'正在合并: {current_file_index + 1}/{len(processed_files)}')

                except Exception as e:
                    raise Exception(f'处理文件时出错（{os.path.basename(pdf_file)}）：{str(e)}')

        return output_doc

    def generate_preview_image(self, files, rows=3, cols=2, orientation='纵向'):
        if not files:
            return None

        processed_files = []
        for file in files:
            if file.lower().endswith('.pdf'):
                temp_pdf = self.process_pdf_page(file)
            else:
                temp_pdf = self.convert_image_to_pdf(file)
            processed_files.append(temp_pdf)

        page_size = fitz.paper_size("a4")
        if orientation == '横向':
            page_size = (page_size[1], page_size[0])

        page_width, page_height = page_size
        files_per_page = rows * cols
        cell_width = page_width / cols
        cell_height = page_height / rows

        preview_doc = fitz.open()
        new_page = preview_doc.new_page(width=page_width, height=page_height)

        for j, pdf_file in enumerate(processed_files[:files_per_page]):
            try:
                row = j // cols
                col = j % cols
                x = col * cell_width
                y = page_height - (row + 1) * cell_height

                src_doc = fitz.open(pdf_file)
                if src_doc.page_count > 0:
                    src_page = src_doc[0]
                    src_rect = src_page.rect
                    src_width = src_rect.width
                    src_height = src_rect.height

                    scale_x = (cell_width * 0.95) / src_width
                    scale_y = (cell_height * 0.95) / src_height
                    scale = min(scale_x, scale_y)

                    scaled_width = src_width * scale
                    scaled_height = src_height * scale
                    centered_x = x + (cell_width - scaled_width) / 2
                    centered_y = y + (cell_height - scaled_height) / 2

                    new_page.show_pdf_page(
                        fitz.Rect(centered_x, centered_y, centered_x + scaled_width, centered_y + scaled_height),
                        src_doc,
                        0
                    )
                src_doc.close()
            except Exception:
                continue

        preview_page = preview_doc[0]
        pix = preview_page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
        img_data = pix.tobytes("ppm")
        preview_doc.close()

        return img_data