from flask import Flask, request, send_file
import os
import csv
import zipfile
import locale
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "generated_certificates"
TEMPLATE_PATH = "certificate_template.png"

# Verificar e definir um caminho seguro para a fonte
DEFAULT_FONT_PATH = "static/fonts/Arial.ttf"
FALLBACK_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_PATH = DEFAULT_FONT_PATH if os.path.exists(DEFAULT_FONT_PATH) else FALLBACK_FONT_PATH

# Criar pastas se não existirem
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Definir localidade para garantir o formato correto da data
try:
    locale.setlocale(locale.LC_TIME, 'pt_BR.utf8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'pt_BR')
    except locale.Error:
        print("Aviso: Não foi possível definir a localidade para português do Brasil.")

# Obter data atual formatada corretamente
def get_current_date():
    return datetime.now().strftime("%d de %B de %Y")

# Garantir que a pasta de saída está vazia antes de gerar novos certificados
def clear_output_folder():
    for file in os.listdir(OUTPUT_FOLDER):
        file_path = os.path.join(OUTPUT_FOLDER, file)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception as e:
            print(f"Erro ao limpar arquivo {file_path}: {e}")

# Gerar certificado para um único aluno
def generate_certificate_for_student(name):
    try:
        template = Image.open(TEMPLATE_PATH)
    except FileNotFoundError:
        return None
    
    clear_output_folder()
    
    date = get_current_date()
    certificate = template.copy()
    draw = ImageDraw.Draw(certificate)
    font = ImageFont.truetype(FONT_PATH, 60)
    draw.text((1100, 700), name, font=font, fill="black")
    draw.text((600, 1050), date, font=font, fill="black")
    
    output_file = os.path.join(OUTPUT_FOLDER, f"{name.replace(' ', '_')}_certificate.png")
    certificate.save(output_file)
    return output_file

# Gerar modelo de CSV
def generate_template_csv():
    template_csv = "name\nBruno Gurgel\nMaria Silva\nJoão Souza"
    template_path = os.path.join(UPLOAD_FOLDER, "template.csv")
    with open(template_path, "w", encoding='utf-8') as f:
        f.write(template_csv)
    return template_path

# Gerar certificados a partir de um CSV
def generate_certificates(csv_path):
    try:
        if not os.path.exists(csv_path):
            return None
        
        template = Image.open(TEMPLATE_PATH)
        clear_output_folder()
        
        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            if "name" not in reader.fieldnames:
                return None
            
            for row in reader:
                name = row["name"].strip()
                if not name:
                    continue
                
                date = get_current_date()
                certificate = template.copy()
                draw = ImageDraw.Draw(certificate)
                font = ImageFont.truetype(FONT_PATH, 60)
                draw.text((1100, 700), name, font=font, fill="black")
                draw.text((600, 1050), date, font=font, fill="black")
                output_file = os.path.join(OUTPUT_FOLDER, f"{name.replace(' ', '_')}_certificate.png")
                certificate.save(output_file)
        
        zip_filename = "certificates.zip"
        zip_path = os.path.join(OUTPUT_FOLDER, zip_filename)
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file in os.listdir(OUTPUT_FOLDER):
                if file.endswith(".png"):
                    zipf.write(os.path.join(OUTPUT_FOLDER, file), file)
        
        return zip_path
    except Exception as e:
        print(f"Erro ao gerar certificados: {e}")
        return None

@app.route('/')
def index():
    current_date = get_current_date()
    return f'''
    <h1>Gerador de Certificados</h1>
    <p>Data que será impressa nos certificados: <strong>{current_date}</strong></p>
    <p><a href="/download_template">Baixar modelo de CSV</a></p>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".csv" required>
        <button type="submit">Enviar</button>
    </form>
    '''

@app.route('/download_template')
def download_template():
    template_path = generate_template_csv()
    return send_file(template_path, as_attachment=True)

@app.route('/aluno', methods=['GET', 'POST'])
def aluno():
    if request.method == 'POST':
        name = request.form.get('name')
        if not name:
            return "Erro: Nome não pode estar vazio."
        certificate_path = generate_certificate_for_student(name)
        if certificate_path:
            return send_file(certificate_path, as_attachment=True)
        return "Erro ao gerar o certificado."
    
    return '''
    <h1>Emitir Certificado</h1>
    <form action="/aluno" method="post">
        <label for="name">Digite seu nome:</label>
        <input type="text" name="name" required>
        <button type="submit">Gerar Certificado</button>
    </form>
    '''

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "Erro: Nenhum arquivo enviado."
    file = request.files['file']
    if file.filename == '':
        return "Erro: Nenhum arquivo selecionado."
    
    file_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(file_path)
    zip_path = generate_certificates(file_path)
    
    if not zip_path:
        return "Erro ao gerar os certificados. Verifique o arquivo CSV."
    
    return f"<h1>Certificados gerados!</h1><p><a href='/download_zip'>Clique aqui para baixar</a></p>"

@app.route('/download_zip')
def download_zip():
    zip_path = os.path.join(OUTPUT_FOLDER, "certificates.zip")
    if not os.path.exists(zip_path):
        return "Erro: Nenhum arquivo ZIP encontrado."
    return send_file(zip_path, as_attachment=True)

@app.route('/favicon.ico')
def favicon():
    return "", 204

if __name__ == '__main__':
    print("Rotas disponíveis:")
    for rule in app.url_map.iter_rules():
        print(rule)
    app.run(host='0.0.0.0', port=8080, threaded=True)