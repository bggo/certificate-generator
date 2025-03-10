from flask import Flask, request, render_template, send_file, jsonify
import base64
import io
import os
import json
import logging
from google.cloud import secretmanager
from google.oauth2 import service_account
from google.cloud import firestore
import csv
import zipfile
import locale
import uuid
import qrcode
from urllib.parse import quote_plus
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont



app = Flask(__name__, static_folder="static")

db = None

### Inicializa Firestore
def get_firestore_client():
    try:
        print("🚀 Iniciando conexão com Firestore...")
        secret_client = secretmanager.SecretManagerServiceClient()

        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or "equilibrion-moodle"
        secret_name = "FIRESTORE_CREDENTIALS"
        version = "latest"
        secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/{version}"

        print(f"🔐 Buscando secret: {secret_path}")

        response = secret_client.access_secret_version(request={"name": secret_path})
        secret_payload = response.payload.data.decode("UTF-8")

        print("✅ Secret recuperado com sucesso!")

        service_account_info = json.loads(secret_payload)

        credentials = service_account.Credentials.from_service_account_info(service_account_info)

        db = firestore.Client(credentials=credentials, project=project_id)

        print("✅ Firestore inicializado e cliente criado!")
        return db

    except Exception as e:
        print(f"❌ Erro ao inicializar Firestore com Secret Manager: {e}")
        return None

db = get_firestore_client()

if db:
    print("✅ Cliente Firestore inicializado e pronto!")
else:
    print("❌ Firestore não inicializado!")

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "generated_certificates"
TEMPLATE_PATH = "static/certificate_template.png"
SIGNATURE_PATH = "static/signature.png"

# Inicializar Firestore
#db = firestore.Client()

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

# Função para salvar certificado no Firestore
def save_certificate_to_firestore(name, date, unique_hash):
    global db 
    try:
        doc_ref = db.collection("certificados").document(unique_hash)
        doc_ref.set({
            "nome": name,
            "data_emissao": date,
            "codigo": unique_hash
        })
        print(f"Certificado salvo no Firestore para {name} (ID: {unique_hash})")
    except Exception as e:
        print(f"Erro ao salvar certificado no Firestore: {e}")

def normalizar_base_url(base_url):
    # Garante que a URL termine com /
    if not base_url.endswith('/'):
        base_url += '/'
    return base_url

def gerar_qr_code(codigo, base_url=None):
    if not base_url:
        try:
            base_url = request.host_url  # já vem com barra na prática
        except RuntimeError:
            base_url = os.getenv("BASE_URL", "http://localhost:8080")

    base_url = normalizar_base_url(base_url)

    # ✅ Concatenando sem barra antes de validar
    rota_validacao = "validar?codigo="
    url_validacao = f"{base_url}{rota_validacao}{codigo}"

    print(f"✅ URL para QRCode gerada: {url_validacao}")

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url_validacao)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
    return qr_img


# Gerar certificado para um único aluno
def generate_certificate_for_student(name, base_url):
    try:
        template = Image.open(TEMPLATE_PATH)
    except FileNotFoundError:
        return None
    
    signature = Image.open(SIGNATURE_PATH).convert("RGBA")
    clear_output_folder()
    
    date = get_current_date()
    unique_hash = str(uuid.uuid4())[:16]  # Gerar hash aleatória de 12 caracteres

    # Gera o QR com a URL dinâmica
    qr_img = gerar_qr_code(unique_hash, base_url)

    certificate = template.copy()
    draw = ImageDraw.Draw(certificate)
    
    
    # Adicionar Nome no certificado    
    font = ImageFont.truetype(FONT_PATH, 60)
    draw.text((1050, 700), name, font=font, fill="black")

    # Adicionar data no certificado
    font_date = ImageFont.truetype(FONT_PATH, 40)
    draw.text((600, 1100), date, font=font_date, fill="black")

    # Adicionar assinatura no certificado
    signature_resized = signature.resize((300, 100))  # Ajustar tamanho conforme necessário
    certificate.paste(signature_resized, (1500, 1050), signature_resized)  # Ajustar posição conforme necessário

    # Adicionar hash pequena no canto inferior
    font_hash = ImageFont.truetype(FONT_PATH, 10)
    draw.text((50, 1400), f"ID: {unique_hash}", font=font_hash, fill="black")

    # Redimensiona para um tamanho discreto
    qr_size = 150  # Tamanho em pixels (ajuste conforme o layout)
    qr_resized = qr_img.resize((qr_size, qr_size))

    # Coordenadas para canto inferior direito
    cert_width, cert_height = certificate.size
    qr_x = cert_width - qr_size - 50  # 50px da borda direita
    qr_y = cert_height - qr_size - 50  # 50px da borda inferior

    # Cola o QR Code no certificado
    certificate.paste(qr_resized, (qr_x, qr_y))

    
    output_file = os.path.join(OUTPUT_FOLDER, f"{name.replace(' ', '_')}_certificate.png")
    certificate.save(output_file)

    # Salvar no Firestore
    save_certificate_to_firestore(name, date, unique_hash)

    return output_file, unique_hash

# Gerar modelo de CSV
def generate_template_csv():
    template_csv = "name\nBruno Gurgel\nMaria Silva\nJoão Souza"
    template_path = os.path.join(UPLOAD_FOLDER, "template.csv")
    with open(template_path, "w", encoding='utf-8') as f:
        f.write(template_csv)
    return template_path

# Gerar certificados a partir de um CSV
def generate_certificates(csv_path, base_url):
    try:
        if not os.path.exists(csv_path):
            return None
        
        template = Image.open(TEMPLATE_PATH)
        signature = Image.open(SIGNATURE_PATH).convert("RGBA")
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
                unique_hash = str(uuid.uuid4())[:16]

                certificate = template.copy()
                draw = ImageDraw.Draw(certificate)

                # Nome
                font = ImageFont.truetype(FONT_PATH, 60)
                draw.text((1050, 700), name, font=font, fill="black")

                # Data
                font_date = ImageFont.truetype(FONT_PATH, 40)
                draw.text((600, 1100), date, font=font_date, fill="black")

                # Assinatura
                signature_resized = signature.resize((300, 100))
                certificate.paste(signature_resized, (1500, 1050), signature_resized)

                # Hash/código
                font_hash = ImageFont.truetype(FONT_PATH, 10)
                draw.text((50, 1400), f"ID: {unique_hash}", font=font_hash, fill="black")

                # 👉 QR Code no canto inferior direito
                qr_img = gerar_qr_code(unique_hash, base_url)
                qr_size = 150  # mesmo tamanho do avulso
                qr_resized = qr_img.resize((qr_size, qr_size))

                cert_width, cert_height = certificate.size
                qr_x = cert_width - qr_size - 50
                qr_y = cert_height - qr_size - 50

                certificate.paste(qr_resized, (qr_x, qr_y))

                # Salvar o certificado
                output_file = os.path.join(OUTPUT_FOLDER, f"{name.replace(' ', '_')}_certificate.png")
                certificate.save(output_file)

                # Salvar dados no Firestore
                save_certificate_to_firestore(name, date, unique_hash)

        # Compactar todos em um zip
        zip_filename = "certificates.zip"
        zip_path = os.path.join(OUTPUT_FOLDER, zip_filename)
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file in os.listdir(OUTPUT_FOLDER):
                if file.endswith(".png"):
                    zipf.write(os.path.join(OUTPUT_FOLDER, file), file)

        return zip_path

    except Exception as e:
        print(f"Erro ao gerar certificados em lote: {e}")
        return None

@app.route('/')
def index():
    return '''
    <html>
    <head>
        <title>Gerador de Certificados</title>
        <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
    </head>
    <body>
        <h1>Bem-vindo ao Gerador de Certificados</h1>
        <ul>
            <li><a href="/aluno">Emitir Certificado Individual</a></li>
            <li><a href="/lote">Emitir Certificados em Lote (CSV)</a></li>
            <li><a href="/validar">Validar Certificado</a></li>
            <li><a href="/listagem">Validar Certificado</a></li>
        </ul>
    </body>
    </html>
    '''

@app.route('/lote')
def lote():
    current_date = get_current_date()
    return f'''
    <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
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

    scheme = request.headers.get('X-Forwarded-Proto', 'https')
    host = request.headers.get('Host')
    base_url = f"{scheme}://{host}"


    if request.method == 'POST':
        name = request.form.get('name')
        if not name:
            return "Erro: Nome não pode estar vazio."

        base_url = request.host_url.rstrip('/')

        # ✅ Chama e captura o caminho e o código único corretamente!
        result = generate_certificate_for_student(name, base_url)

        # ✅ Se não veio nada, erro!
        if not result:
            return "Erro ao gerar o certificado."

        certificate_path, unique_hash = result

        # ✅ Se o código veio vazio, erro!
        if not unique_hash:
            return "Erro ao gerar o código do certificado."

        # ✅ Monta o link de validação e compartilhamento com o código correto!
        validar_url = f"{base_url}/validar?codigo={unique_hash}"
        validar_url_encoded = quote_plus(validar_url)  # 🔥 encodando!
        linkedin_share_url = f"https://www.linkedin.com/sharing/share-offsite/?url={validar_url_encoded}"


        # 🔎 LOGS PARA DEBUG!
        print("DEBUG INFO:")
        print(f"Base URL: {base_url}")
        print(f"Unique Hash: {unique_hash}")
        print(f"Cert Path: {certificate_path}")
        print(f"Validar URL: {validar_url}")
        print(f"LinkedIn URL: {linkedin_share_url}")

        # ✅ Gera o Base64 da imagem para exibir na tela
        import base64
        with open(certificate_path, "rb") as image_file:
            img_base64 = base64.b64encode(image_file.read()).decode('utf-8')

        # ✅ Retorna a página HTML com a imagem e os links
        return f'''
        <html>
        <head>
            <title>Certificado Gerado</title>
            <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
            <style>
                .cert-image {{
                    max-width: 600px;
                    margin-top: 30px;
                    border: 1px solid #ccc;
                    box-shadow: 0px 0px 10px rgba(0,0,0,0.1);
                }}
                .button-container {{
                    margin-top: 20px;
                }}
                .button-container a {{
                    display: inline-block;
                    margin-right: 10px;
                    padding: 10px 20px;
                    background-color: #4CAF50;
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                }}
                .button-container a:hover {{
                    background-color: #45a049;
                }}
            </style>
        </head>
        <body>
            <h1>🎉 Certificado Gerado!</h1>

            <img class="cert-image" src="data:image/png;base64,{img_base64}" alt="Certificado">

            <div class="button-container">
                <a href="{base_url}/download_cert/{unique_hash}">⬇️ Baixar Certificado</a>
                <a href="{linkedin_share_url}" target="_blank">🔗 Compartilhar no LinkedIn</a>
                <a href="/">🔙 Voltar ao Início</a>
            </div>
        </body>
        </html>
        '''

    # Se for GET
    base_url = request.host_url.rstrip('/')
    return f'''
    <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
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

    # ✅ Captura o host dinamicamente
    base_url = request.host_url.rstrip('/')  # Exemplo: https://certificate-generator.run.app

    # ✅ Agora passando os dois argumentos!
    zip_path = generate_certificates(file_path, base_url)

    if not zip_path:
        return "Erro ao gerar os certificados. Verifique o arquivo CSV."

    return '''
    <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
    <h1>Certificados gerados!</h1><p><a href='/download_zip'>Clique aqui para baixar</a></p>
    '''

@app.route('/test_firestore', methods=['GET'])
def test_firestore():
    global db  # <-- isso garante que ele acessa a variável global
    if db is None:
        app.logger.error("❌ Firestore não foi inicializado corretamente!")
        return jsonify({"status": "❌ Firestore não foi inicializado corretamente!"}), 500

    try:
        doc_ref = db.collection("certificados").document("teste")
        doc_ref.set({
            "nome": "Usuário de Teste",
            "data_emissao": "10 de Março de 2025",
            "codigo": "TESTE123"
        })

        app.logger.info("✅ Documento salvo no Firestore!")
        return jsonify({"status": "✅ Firestore está funcionando! Documento de teste criado."})

    except Exception as e:
        app.logger.error(f"❌ ERRO DETALHADO NO FIRESTORE: {e}")
        return jsonify({"status": "❌ Erro ao salvar no Firestore", "erro": str(e)}), 500

## Rota de validação
@app.route('/validar', methods=['GET', 'POST'])
def validar_certificado():
    global db
    if db is None:
        return "❌ Firestore não inicializado!", 500

    codigo = None

    # 1️⃣ Se for POST (formulário enviado)
    if request.method == 'POST':
        codigo = request.form.get('codigo')

    # 2️⃣ Se for GET com parâmetro na URL
    if request.method == 'GET' and request.args.get('codigo'):
        codigo = request.args.get('codigo')

    # Se ainda não tem código, exibe o formulário
    if not codigo:
        return '''
        <html>
        <head>
            <title>Validação de Certificado</title>
            <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
        </head>
        <body>
            <h1>🔎 Validar Certificado</h1>
            <form method="post">
                <input type="text" name="codigo" placeholder="Digite o código do certificado" required />
                <br><br>
                <button type="submit">Validar</button>
            </form>
            <br>
            <a class="back-link" href="/">🔙 Voltar ao início</a>
        </body>
        </html>
        '''

    # Agora tem código, vamos validar
    try:
        print(f"🔍 Validando certificado com ID: {codigo}")

        doc_ref = db.collection("certificados").document(codigo)
        doc = doc_ref.get()

        if not doc.exists:
            print("❌ Documento não encontrado no Firestore.")
            return '''
            <html>
            <head>
                <title>Certificado Não Encontrado</title>
                <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
            </head>
            <body>
                <h1>❌ Certificado não encontrado!</h1>
                <p>Verifique o código e tente novamente.</p>
                <a class="back-link" href="/validar">🔙 Tentar outro código</a>
            </body>
            </html>
            ''', 404

        # Recuperar dados
        data = doc.to_dict()
        nome = data.get('nome')
        data_emissao = data.get('data_emissao')

        print(f"✅ Certificado válido para {nome} - {data_emissao}")

        # Gerar certificado para exibir
        try:
            template = Image.open(TEMPLATE_PATH)
            signature = Image.open(SIGNATURE_PATH).convert("RGBA")

            certificate = template.copy()
            draw = ImageDraw.Draw(certificate)

            # Nome
            font = ImageFont.truetype(FONT_PATH, 60)
            draw.text((1050, 700), nome, font=font, fill="black")

            # Data de emissão
            font_date = ImageFont.truetype(FONT_PATH, 40)
            draw.text((600, 1100), data_emissao, font=font_date, fill="black")

            # Assinatura
            signature_resized = signature.resize((300, 100))
            certificate.paste(signature_resized, (1500, 1050), signature_resized)

            # Código
            font_hash = ImageFont.truetype(FONT_PATH, 10)
            draw.text((50, 1400), f"ID: {codigo}", font=font_hash, fill="black")

            # Base64 para exibir inline
            img_io = io.BytesIO()
            certificate.save(img_io, 'PNG')
            img_io.seek(0)

            import base64
            img_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')

        except Exception as e:
            print(f"❌ Erro ao gerar imagem do certificado: {e}")
            img_base64 = None

        # HTML com a resposta e CSS EXTERNO
        return f'''
        <html>
        <head>
            <title>Validação de Certificado</title>
            <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
            <style>
                .cert-image {{
                    max-width: 600px;
                    margin-top: 30px;
                    border: 1px solid #ccc;
                    box-shadow: 0px 0px 10px rgba(0,0,0,0.1);
                }}
                .success {{
                    color: #4CAF50;
                    font-size: 22px;
                    margin-bottom: 10px;
                }}
            </style>
        </head>
        <body>
            <h1 class="success">✅ Certificado válido!</h1>
            <div>
                <p><strong>Nome:</strong> {nome}</p>
                <p><strong>Data de Emissão:</strong> {data_emissao}</p>
                <p><strong>ID de Validação:</strong> {codigo}</p>
            </div>

            {'<img class="cert-image" src="data:image/png;base64,' + img_base64 + '">' if img_base64 else '<p>Erro ao carregar a imagem do certificado.</p>'}

            <a class="back-link" href="/validar">🔙 Validar outro certificado</a>
        </body>
        </html>
        '''

    except Exception as e:
        print(f"❌ Erro inesperado na validação: {e}")
        return '''
        <html>
        <head>
            <title>Erro na Validação</title>
            <link rel="stylesheet" href="https://certificate-generator-194178149694.us-central1.run.app/static/styles.css">
        </head>
        <body>
            <h1>❌ Erro ao validar certificado!</h1>
            <p>Tente novamente mais tarde.</p>
            <a class="back-link" href="/validar">🔙 Voltar para validação</a>
        </body>
        </html>
        ''', 500


## Rota para remontar o certificado na consulta!
@app.route('/certificado/<codigo>')
def mostrar_certificado(codigo):
    global db
    try:
        print(f"🔍 Buscando certificado com ID: {codigo}")

        # 1. Buscar o documento do certificado
        doc_ref = db.collection("certificados").document(codigo)
        doc = doc_ref.get()

        if not doc.exists:
            print("❌ Documento não encontrado no Firestore.")
            return "❌ Certificado não encontrado!", 404

        data = doc.to_dict()
        nome = data.get('nome')
        data_emissao = data.get('data_emissao')

        print(f"✅ Documento encontrado: Nome={nome}, Data={data_emissao}")

        # 2. Carregar template
        try:
            template = Image.open(TEMPLATE_PATH)
            print(f"✅ Template carregado: {TEMPLATE_PATH}")
        except Exception as e:
            print(f"❌ Erro ao carregar o template: {e}")
            return "❌ Erro ao carregar o template!", 500

        # 3. Carregar assinatura
        try:
            signature = Image.open(SIGNATURE_PATH).convert("RGBA")
            print(f"✅ Assinatura carregada: {SIGNATURE_PATH}")
        except Exception as e:
            print(f"❌ Erro ao carregar a assinatura: {e}")
            return "❌ Erro ao carregar a assinatura!", 500

        # 4. Montar o certificado
        certificate = template.copy()
        draw = ImageDraw.Draw(certificate)

        # Nome
        font = ImageFont.truetype(FONT_PATH, 60)
        draw.text((1050, 700), nome, font=font, fill="black")

        # Data de emissão
        font_date = ImageFont.truetype(FONT_PATH, 40)
        draw.text((600, 1100), data_emissao, font=font_date, fill="black")

        # Assinatura
        signature_resized = signature.resize((300, 100))
        certificate.paste(signature_resized, (1500, 1050), signature_resized)

        # Hash/código
        font_hash = ImageFont.truetype(FONT_PATH, 10)
        draw.text((50, 1400), f"ID: {codigo}", font=font_hash, fill="black")

        # 5. Salvar a imagem no buffer e retornar
        img_io = io.BytesIO()
        certificate.save(img_io, 'PNG')
        img_io.seek(0)

        print("✅ Certificado gerado com sucesso!")
        return send_file(img_io, mimetype='image/png')

    except Exception as e:
        print(f"❌ Erro inesperado ao gerar certificado dinâmico: {e}")
        return "❌ Erro ao gerar certificado!", 500

@app.route('/download_zip')
def download_zip():
    zip_path = os.path.join(OUTPUT_FOLDER, "certificates.zip")
    if not os.path.exists(zip_path):
        return "Erro: Nenhum arquivo ZIP encontrado."
    return send_file(zip_path, as_attachment=True)

@app.route('/download_cert/<codigo>')
def download_certificado(codigo):
    global db

    try:
        print(f"🔍 Download do certificado com ID: {codigo}")

        doc_ref = db.collection("certificados").document(codigo)
        doc = doc_ref.get()

        if not doc.exists:
            print("❌ Certificado não encontrado para download!")
            return "❌ Certificado não encontrado!", 404

        data = doc.to_dict()
        nome = data.get('nome')
        data_emissao = data.get('data_emissao')

        # Gera novamente a imagem do certificado
        template = Image.open(TEMPLATE_PATH)
        signature = Image.open(SIGNATURE_PATH).convert("RGBA")

        certificate = template.copy()
        draw = ImageDraw.Draw(certificate)

        # Nome
        font = ImageFont.truetype(FONT_PATH, 60)
        draw.text((1050, 700), nome, font=font, fill="black")

        # Data de emissão
        font_date = ImageFont.truetype(FONT_PATH, 40)
        draw.text((600, 1100), data_emissao, font=font_date, fill="black")

        # Assinatura
        signature_resized = signature.resize((300, 100))
        certificate.paste(signature_resized, (1500, 1050), signature_resized)

        # Código/Hash
        font_hash = ImageFont.truetype(FONT_PATH, 10)
        draw.text((50, 1400), f"ID: {codigo}", font=font_hash, fill="black")

        # QR Code
        base_url = request.host_url
        qr_img = gerar_qr_code(codigo, base_url)

        qr_size = 150
        qr_resized = qr_img.resize((qr_size, qr_size))

        cert_width, cert_height = certificate.size
        qr_x = cert_width - qr_size - 50
        qr_y = cert_height - qr_size - 50
        certificate.paste(qr_resized, (qr_x, qr_y))

        # Salvar no buffer e retornar
        img_io = io.BytesIO()
        certificate.save(img_io, 'PNG')
        img_io.seek(0)

        filename = f"{nome.replace(' ', '_')}_certificado.png"

        print(f"✅ Download do certificado pronto: {filename}")
        return send_file(img_io, mimetype='image/png', as_attachment=True, download_name=filename)

    except Exception as e:
        print(f"❌ Erro ao preparar download: {e}")
        return "❌ Erro ao preparar o certificado para download!", 500

@app.route('/favicon.ico')
def favicon():
    return "", 204


@app.route('/listagem')
def listar_certificados():
    global db

    if db is None:
        return "❌ Firestore não foi inicializado!", 500

    try:
        # Busca todos os certificados
        certificados_ref = db.collection("certificados")
        certificados_docs = certificados_ref.stream()

        # Constrói uma lista com os dados
        certificados = []
        for doc in certificados_docs:
            data = doc.to_dict()
            certificados.append({
                "nome": data.get('nome'),
                "data_emissao": data.get('data_emissao'),
                "codigo": data.get('codigo')
            })

        # Garante ordenação por data ou nome (opcional)
        certificados.sort(key=lambda x: x['nome'])

        # Monta o base_url seguro
        scheme = request.headers.get('X-Forwarded-Proto', 'https')
        host = request.headers.get('Host')
        base_url = f"{scheme}://{host}"

        # Cria o HTML
        table_rows = ""
        for cert in certificados:
            validar_url = f"{base_url}/validar?codigo={cert['codigo']}"
            download_url = f"{base_url}/download_cert/{cert['codigo']}"
            table_rows += f"""
                <tr>
                    <td>{cert['nome']}</td>
                    <td>{cert['data_emissao']}</td>
                    <td>{cert['codigo']}</td>
                    <td>
                        <a href="{validar_url}" target="_blank">🔎 Validar</a> |
                        <a href="{download_url}">⬇️ Baixar</a>
                    </td>
                </tr>
            """

        # Retorna a página com o CSS já aplicado
        return f"""
        <html>
        <head>
            <title>Lista de Certificados Emitidos</title>
            <link rel="stylesheet" href="{base_url}/static/styles.css">
            <style>
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin-top: 20px;
                }}
                th, td {{
                    text-align: left;
                    padding: 8px;
                    border-bottom: 1px solid #ddd;
                }}
                th {{
                    background-color: #f2f2f2;
                }}
                tr:hover {{background-color: #f5f5f5;}}
                .back-link {{
                    margin-top: 20px;
                    display: inline-block;
                }}
            </style>
        </head>
        <body>
            <h1>📋 Lista de Certificados Emitidos</h1>
            <table>
                <tr>
                    <th>Nome</th>
                    <th>Data de Emissão</th>
                    <th>ID</th>
                    <th>Ações</th>
                </tr>
                {table_rows}
            </table>
            <a class="back-link" href="/">🔙 Voltar ao Início</a>
        </body>
        </html>
        """

    except Exception as e:
        print(f"❌ Erro ao listar certificados: {e}")
        return f"❌ Erro ao listar certificados: {e}", 500

##### Turmas

@app.route('/turmas/criar', methods=['GET', 'POST'])
def criar_turma():
    global db
    if db is None:
        return "❌ Firestore não inicializado!", 500

    base_url = request.host_url.rstrip('/')

    if request.method == 'POST':
        nome = request.form.get('nome')
        data_evento = request.form.get('data_evento')

        if not nome or not data_evento:
            return "❌ Nome e Data do Evento são obrigatórios."

        try:
            turma_id = str(uuid.uuid4())[:16]  # ID único da turma

            # Salva no Firestore na coleção "turmas"
            doc_ref = db.collection("turmas").document(turma_id)
            doc_ref.set({
                "id": turma_id,
                "nome": nome,
                "data_evento": data_evento
            })

            print(f"✅ Turma criada: {nome} - {data_evento} (ID: {turma_id})")

            return f'''
            <html>
            <head>
                <title>Turma Criada</title>
                <link rel="stylesheet" href="{base_url}/static/styles.css">
            </head>
            <body>
                <h1>✅ Turma Criada com Sucesso!</h1>
                <p><strong>Nome da Turma:</strong> {nome}</p>
                <p><strong>Data do Evento:</strong> {data_evento}</p>
                <p><strong>ID da Turma:</strong> {turma_id}</p>
                <a href="/turmas/criar">➕ Criar Nova Turma</a><br>
                <a href="/turmas">📋 Ver Turmas Criadas</a><br>
                <a href="/">🔙 Voltar ao Início</a>
            </body>
            </html>
            '''

        except Exception as e:
            print(f"❌ Erro ao criar turma: {e}")
            return f"❌ Erro ao criar turma: {e}", 500

    # Se for GET, exibe o formulário
    return f'''
    <html>
    <head>
        <title>Criar Nova Turma</title>
        <link rel="stylesheet" href="{base_url}/static/styles.css">
    </head>
    <body>
        <h1>➕ Criar Nova Turma</h1>
        <form method="post">
            <label for="nome">Nome da Turma:</label><br>
            <input type="text" id="nome" name="nome" required><br><br>

            <label for="data_evento">Data do Evento:</label><br>
            <input type="date" id="data_evento" name="data_evento" required><br><br>

            <button type="submit">Criar Turma</button>
        </form>
        <br>
        <a href="/">🔙 Voltar ao Início</a>
    </body>
    </html>
    '''


@app.route('/turmas')
def listar_turmas():
    global db
    if db is None:
        return "❌ Firestore não inicializado!", 500

    try:
        turmas_ref = db.collection("turmas")
        turmas_docs = turmas_ref.stream()

        turmas = []
        for doc in turmas_docs:
            data = doc.to_dict()
            turmas.append({
                "id": data.get('id'),
                "nome": data.get('nome'),
                "data_evento": data.get('data_evento')
            })

        # Ordena pelo nome da turma (opcional)
        turmas.sort(key=lambda x: x['nome'])

        base_url = request.host_url.rstrip('/')

        table_rows = ""
        for turma in turmas:
            table_rows += f"""
                <tr>
                    <td>{turma['id']}</td>
                    <td>{turma['nome']}</td>
                    <td>{turma['data_evento']}</td>
                </tr>
            """

        return f'''
        <html>
        <head>
            <title>Lista de Turmas</title>
            <link rel="stylesheet" href="{base_url}/static/styles.css">
            <style>
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin-top: 20px;
                }}
                th, td {{
                    text-align: left;
                    padding: 8px;
                    border-bottom: 1px solid #ddd;
                }}
                th {{
                    background-color: #f2f2f2;
                }}
                tr:hover {{background-color: #f5f5f5;}}
                .back-link {{
                    margin-top: 20px;
                    display: inline-block;
                }}
            </style>
        </head>
        <body>
            <h1>📋 Lista de Turmas</h1>
            <table>
                <tr>
                    <th>ID da Turma</th>
                    <th>Nome da Turma</th>
                    <th>Data do Evento</th>
                </tr>
                {table_rows}
            </table>
            <br>
            <a href="/turmas/criar">➕ Criar Nova Turma</a><br>
            <a href="/">🔙 Voltar ao Início</a>
        </body>
        </html>
        '''

    except Exception as e:
        print(f"❌ Erro ao listar turmas: {e}")
        return f"❌ Erro ao listar turmas: {e}", 500



if __name__ == '__main__':
    print("Rotas disponíveis:")
    for rule in app.url_map.iter_rules():
        print(rule)
    app.run(host='0.0.0.0', port=8080, threaded=True)