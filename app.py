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
import logging

# Configura o logger básico
logging.basicConfig(
    level=logging.INFO,  # Pode trocar para DEBUG se quiser mais detalhe
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# Cria o logger
logger = logging.getLogger(__name__)



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


### Garantir tudo como
def get_secure_base_url():
    scheme = request.headers.get('X-Forwarded-Proto', 'https')
    host = request.headers.get('Host')
    
    # Força HTTPS se não for
    if not scheme or scheme != 'https':
        scheme = 'https'

    return f"{scheme}://{host}"


db = get_firestore_client()

if db:
    print("✅ Cliente Firestore inicializado e pronto!")
else:
    print("❌ Firestore não inicializado!")

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "generated_certificates"
TEMPLATE_PATH = "static/certificate.png"
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

def get_font_by_name_length(name):
    length = len(name)

    if length <= 12:
        font_size = 60
    elif length <= 20:
        font_size = 50
    elif length <= 30:
        font_size = 40
    else:
        font_size = 30

    print(f"✅ Nome: {name} (len: {length}) | Usando fonte de tamanho {font_size}")

    # Usa o caminho já definido anteriormente
    if os.path.exists(DEFAULT_FONT_PATH):
        font_path = DEFAULT_FONT_PATH
    elif os.path.exists(FALLBACK_FONT_PATH):
        font_path = FALLBACK_FONT_PATH
    else:
        raise FileNotFoundError("❌ Nenhuma fonte disponível encontrada!")
    
    return ImageFont.truetype(font_path, font_size)


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
def save_certificate_to_firestore(
    nome,
    data_emissao,
    codigo,
    turma_nome=None,
    data_evento=None,
    nome_treinamento=None,
    carga_horaria=None
):
    global db

    try:
        logger.info(f"💾 Salvando certificado no Firestore: Nome={nome}, Código={codigo}")

        if db is None:
            logger.error("❌ Firestore não inicializado!")
            return False

        # Monta o dicionário com os dados obrigatórios
        certificado_data = {
            'nome': nome,
            'data_emissao': data_emissao,
            'codigo': codigo
        }

        # Adiciona as informações opcionais se estiverem disponíveis
        if turma_nome:
            certificado_data['turma_nome'] = turma_nome
        if data_evento:
            certificado_data['data_evento'] = data_evento
        if nome_treinamento:
            certificado_data['nome_treinamento'] = nome_treinamento
        if carga_horaria:
            certificado_data['carga_horaria'] = carga_horaria

        # Salva ou atualiza no Firestore
        db.collection("certificados").document(codigo).set(certificado_data)

        logger.info(f"✅ Certificado salvo no Firestore com sucesso! Dados: {certificado_data}")
        return True

    except Exception as e:
        logger.error(f"❌ Erro ao salvar certificado no Firestore: {e}")
        return False


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

def montar_certificado_imagem(
    nome,
    data_emissao,
    codigo,
    base_url,
    turma_nome=None,
    data_evento=None,
    nome_treinamento=None,
    carga_horaria=None
):
    try:
        logger.info(f"🖼️ Iniciando montagem do certificado para {nome} (ID: {codigo})")

        # === Carrega o template ===
        try:
            template = Image.open(TEMPLATE_PATH)
            logger.info(f"✅ Template carregado com sucesso: {TEMPLATE_PATH}")
        except Exception as e:
            logger.error(f"❌ Erro ao carregar o template: {e}")
            return None

        # === Carrega a assinatura ===
        try:
            signature = Image.open(SIGNATURE_PATH).convert("RGBA")
            logger.info(f"✅ Assinatura carregada com sucesso: {SIGNATURE_PATH}")
        except Exception as e:
            logger.error(f"❌ Erro ao carregar a assinatura: {e}")
            return None

        # === Prepara a cópia do template para desenhar ===
        certificate = template.copy()
        draw = ImageDraw.Draw(certificate)

        # === NOME DO PARTICIPANTE ===
        try:
            font_nome = get_font_by_name_length(nome)

            bbox = draw.textbbox((0, 0), nome, font=font_nome)
            text_width = bbox[2] - bbox[0]
            cert_width, _ = certificate.size
            
            offset_x = 200  # ➡️ Ajuste esse valor para calibrar
            nome_x = (cert_width - text_width) / 2 + offset_x
            nome_y = 650

            logger.info(f"✍️ Desenhando nome: '{nome}' (Fonte: {font_nome.size}px) em x={nome_x}, y={nome_y}")
            draw.text((nome_x, nome_y), nome, font=font_nome, fill="black")

        except Exception as e:
            logger.error(f"❌ Erro ao desenhar o nome no certificado: {e}")
            return None

        # === DATA DE EMISSÃO ===
        try:
            font_date = ImageFont.truetype(FONT_PATH, 40)
            draw.text((600, 1100), data_emissao, font=font_date, fill="black")
            logger.info(f"🗓️ Data de emissão desenhada: {data_emissao}")

        except Exception as e:
            logger.error(f"❌ Erro ao desenhar a data de emissão: {e}")
            return None

        # === ASSINATURA ===
        try:
            signature_resized = signature.resize((300, 100))
            certificate.paste(signature_resized, (1500, 1050), signature_resized)
            logger.info("🖋️ Assinatura colada com sucesso!")

        except Exception as e:
            logger.error(f"❌ Erro ao colar a assinatura: {e}")
            return None

        # === CÓDIGO/ID ===
        try:
            font_hash = ImageFont.truetype(FONT_PATH, 10)
            codigo_texto = f"ID: {codigo}"
            draw.text((50, 1400), codigo_texto, font=font_hash, fill="black")
            logger.info(f"🔐 Código desenhado: {codigo_texto}")

        except Exception as e:
            logger.error(f"❌ Erro ao desenhar o código/ID: {e}")
            return None

        # === QR CODE ===
        try:
            qr_img = gerar_qr_code(codigo, base_url)
            qr_size = 150
            qr_resized = qr_img.resize((qr_size, qr_size))

            cert_width, cert_height = certificate.size
            qr_x = cert_width - qr_size - 50
            qr_y = cert_height - qr_size - 50

            certificate.paste(qr_resized, (qr_x, qr_y))
            logger.info(f"📲 QR Code colado na posição x={qr_x}, y={qr_y}")

        except Exception as e:
            logger.error(f"❌ Erro ao gerar ou colar o QR Code: {e}")
            return None

        # === INFORMAÇÕES ADICIONAIS ===
        # === INFORMAÇÕES ADICIONAIS ===
        try:
            font_info = ImageFont.truetype(FONT_PATH, 20)
            font_info_title = ImageFont.truetype(FONT_PATH, 35)

            # 🔹 Parte 1: Informações que ficam no laço (Turma e Data do Evento)
            info_lines = []
            if turma_nome:
                info_lines.append(f"Turma: {turma_nome}")
            if data_evento:
                info_lines.append(f"Data do evento: {data_evento}")

            info_x = 50
            start_y = 1320  # Começa antes para dar espaço
            line_height = 25

            for i, line in enumerate(info_lines):
                y = start_y + i * line_height
                draw.text((info_x, y), line, font=font_info, fill="black")
                logger.info(f"📝 Informação adicional desenhada: {line} em x={info_x}, y={y}")

            # 🔹 Parte 2: Nome do treinamento (posição personalizada)
            if nome_treinamento:
                treinamento_x = 600  # ➡️ Altere conforme o template
                treinamento_y = 900  # ➡️ Altere conforme o template
                treinamento_text = f"{nome_treinamento}"
                draw.text((treinamento_x, treinamento_y), treinamento_text, font=font_info_title, fill="black")
                logger.info(f"📝 Nome do treinamento desenhado: {treinamento_text} em x={treinamento_x}, y={treinamento_y}")

            # 🔹 Parte 3: Carga horária (posição personalizada)
            if carga_horaria:
                carga_x = 600  # ➡️ Altere conforme o template
                carga_y = 1380  # ➡️ Altere conforme o template
                carga_text = f"Carga horária: {carga_horaria}h"
                draw.text((carga_x, carga_y), carga_text, font=font_info, fill="black")
                logger.info(f"📝 Carga horária desenhada: {carga_text} em x={carga_x}, y={carga_y}")

        except Exception as e:
            logger.error(f"❌ Erro ao desenhar informações adicionais: {e}")
            return None

        logger.info(f"🎉 Certificado montado com sucesso para {nome}!")
        return certificate

    except Exception as e:
        logger.error(f"❌ Erro inesperado ao montar certificado: {e}")
        return None



def generate_certificate_for_student(
    name,
    base_url,
    nome_turma=None,
    data_evento=None,
    nome_treinamento=None,
    carga_horaria=None
):
    try:
        logger.info(f"🚀 Iniciando geração de certificado para estudante: {name}")

        # Tenta abrir o template
        try:
            template = Image.open(TEMPLATE_PATH)
            logger.info(f"✅ Template carregado: {TEMPLATE_PATH}")
        except FileNotFoundError:
            logger.error(f"❌ Template não encontrado em {TEMPLATE_PATH}")
            return None

        # Tenta abrir a assinatura
        try:
            signature = Image.open(SIGNATURE_PATH).convert("RGBA")
            logger.info(f"✅ Assinatura carregada: {SIGNATURE_PATH}")
        except FileNotFoundError:
            logger.error(f"❌ Assinatura não encontrada em {SIGNATURE_PATH}")
            return None

        # Limpa a pasta de saída
        clear_output_folder()
        logger.info(f"🧹 Pasta {OUTPUT_FOLDER} limpa para novos certificados")

        # Define a data de emissão e gera o código único
        date = get_current_date()
        unique_hash = str(uuid.uuid4())[:16]
        logger.info(f"📅 Data de emissão: {date} | 🔐 Código único gerado: {unique_hash}")

        # ✅ Garantir que todos os campos tenham valor (fallbacks)
        if not nome_turma:
            logger.warning(f"⚠️ Nome da turma não informado para {name}")
            nome_turma = "Turma não especificada"

        if not data_evento:
            logger.warning(f"⚠️ Data do evento não informada para {name}")
            data_evento = "Data não informada"

        if not nome_treinamento:
            logger.warning(f"⚠️ Nome do treinamento não informado para {name}")
            nome_treinamento = "Treinamento não especificado"

        if not carga_horaria:
            logger.warning(f"⚠️ Carga horária não informada para {name}")
            carga_horaria = "Carga horária não informada"

        # Monta o certificado com as novas informações
        certificate = montar_certificado_imagem(
            nome=name,
            data_emissao=date,
            codigo=unique_hash,
            base_url=base_url,
            turma_nome=nome_turma,
            data_evento=data_evento,
            nome_treinamento=nome_treinamento,
            carga_horaria=carga_horaria
        )

        if not certificate:
            logger.error(f"❌ Falha ao montar o certificado para {name}")
            return None

        # Salva o certificado como imagem
        output_file = os.path.join(OUTPUT_FOLDER, f"{name.replace(' ', '_')}_certificate.png")
        certificate.save(output_file)
        logger.info(f"✅ Certificado salvo em {output_file}")

        # Salva no Firestore com todos os dados
        save_certificate_to_firestore(
            nome=name,
            data_emissao=date,
            codigo=unique_hash,
            turma_nome=nome_turma,
            data_evento=data_evento,
            nome_treinamento=nome_treinamento,
            carga_horaria=carga_horaria
        )
        logger.info(f"✅ Dados do certificado salvos no Firestore para {name} (ID: {unique_hash})")

        logger.info(f"🎉 Certificado gerado com sucesso para {name}")
        return output_file, unique_hash

    except Exception as e:
        logger.error(f"❌ Erro inesperado ao gerar certificado para {name}: {e}")
        return None




# Gerar modelo de CSV
def generate_template_csv():
    template_csv = "name\nBruno Gurgel\nMaria Silva\nJoão Souza"
    template_path = os.path.join(UPLOAD_FOLDER, "template.csv")
    with open(template_path, "w", encoding='utf-8') as f:
        f.write(template_csv)
    return template_path

def generate_certificates(csv_path, base_url, turma_id):
    try:
        logger.info(f"🚀 Iniciando geração de certificados em lote para a turma {turma_id}")

        # ✅ Verifica se o CSV existe
        if not os.path.exists(csv_path):
            logger.error(f"❌ Arquivo CSV não encontrado: {csv_path}")
            return None

        # ✅ Busca dados da turma no Firestore
        turma_ref = db.collection("turmas").document(turma_id)
        turma_doc = turma_ref.get()

        if not turma_doc.exists:
            logger.error(f"❌ Turma com ID {turma_id} não encontrada no Firestore")
            return None

        turma_data = turma_doc.to_dict()

        # ✅ Captura todos os dados relevantes da turma
        nome_turma = turma_data.get("nome", "Turma sem nome")
        data_evento = turma_data.get("data_evento", "Data do evento não informada")
        nome_treinamento = turma_data.get("nome_treinamento", "Treinamento não especificado")
        carga_horaria = turma_data.get("carga_horaria", "Carga horária não informada")

        logger.info(f"✅ Turma encontrada: {nome_turma} | Data do evento: {data_evento} | Treinamento: {nome_treinamento} | Carga horária: {carga_horaria}")

        # ✅ Limpa a pasta de saída
        clear_output_folder()
        logger.info(f"🧹 Pasta {OUTPUT_FOLDER} limpa para novos certificados")

        # ✅ Processa o CSV
        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)

            if "name" not in reader.fieldnames:
                logger.error("❌ CSV inválido. Coluna 'name' não encontrada!")
                return None

            # ✅ Processa cada linha do CSV
            for row in reader:
                name = row["name"].strip()

                if not name:
                    logger.warning("⚠️ Nome vazio encontrado no CSV, pulando...")
                    continue

                logger.info(f"📝 Gerando certificado para: {name}")

                date = get_current_date()
                unique_hash = str(uuid.uuid4())[:16]

                # ✅ Gera o certificado com as infos completas
                certificate = montar_certificado_imagem(
                    nome=name,
                    data_emissao=date,
                    codigo=unique_hash,
                    base_url=base_url,
                    turma_nome=nome_turma,
                    data_evento=data_evento,
                    nome_treinamento=nome_treinamento,
                    carga_horaria=carga_horaria
                )

                if not certificate:
                    logger.error(f"❌ Falha ao montar certificado para {name}, continuando para o próximo...")
                    continue

                # ✅ Salva o certificado na pasta de saída
                output_file = os.path.join(OUTPUT_FOLDER, f"{name.replace(' ', '_')}_certificate.png")
                certificate.save(output_file)
                logger.info(f"✅ Certificado salvo: {output_file}")

                # ✅ Salva dados no Firestore com todas as informações
                save_certificate_to_firestore(
                    nome=name,
                    data_emissao=date,
                    codigo=unique_hash,
                    turma_nome=nome_turma,
                    data_evento=data_evento,
                    nome_treinamento=nome_treinamento,
                    carga_horaria=carga_horaria
                )

        # ✅ Compacta tudo em um arquivo ZIP
        zip_filename = "certificates.zip"
        zip_path = os.path.join(OUTPUT_FOLDER, zip_filename)

        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file in os.listdir(OUTPUT_FOLDER):
                if file.endswith(".png"):
                    zipf.write(os.path.join(OUTPUT_FOLDER, file), file)

        logger.info(f"✅ Certificados em lote gerados e compactados com sucesso: {zip_path}")

        return zip_path

    except Exception as e:
        logger.error(f"❌ Erro ao gerar certificados em lote: {e}")
        return None


@app.route('/')
def index():
    base_url = get_secure_base_url()

    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Gerador de Certificados</title>
        <link rel="stylesheet" href="{base_url}/static/styles.css">
    </head>
    <body>
        <div class="container">
            <h1>🎓 Bem-vindo ao Gerador de Certificados</h1>

            <div class="button-container">
                <a href="/aluno" class="btn">🧑‍🎓 Emitir Certificado Individual</a>
                <a href="/lote" class="btn">📂 Emitir Certificados em Lote (CSV)</a>
                <a href="/turmas" class="btn">📋 Gerenciar Turmas</a>
                <a href="/validar" class="btn">🔎 Validar Certificado</a>
                <a href="/listagem" class="btn">📜 Listagem de Certificados</a>
            </div>

            <footer>
                <p>&copy; {datetime.now().year} EquilibriON | Gerador de Certificados</p>
            </footer>
        </div>
    </body>
    </html>
    '''


@app.route('/lote')
def lote():
    base_url = get_secure_base_url()
    current_date = get_current_date()

    return f'''
    <html>
    <head>
        <title>Gerar Certificados em Lote</title>
        <link rel="stylesheet" href="{base_url}/static/styles.css">
    </head>
    <body>
        <h1>Gerador de Certificados em Lote</h1>

        <p><strong>Data que será impressa nos certificados individuais:</strong> {current_date}</p>
        
        <p><a href="/download_template">⬇️ Baixar modelo de CSV</a></p>

        <form action="/upload" method="post" enctype="multipart/form-data">
            <label for="file">Selecione o arquivo CSV:</label><br>
            <input type="file" name="file" accept=".csv" required><br><br>

            <label for="turma_id">Digite o código da turma:</label><br>
            <input type="text" name="turma_id" required><br><br>

            <button type="submit">Gerar Certificados em Lote</button>
        </form>
    </body>
    </html>
    '''

@app.route('/download_template')
def download_template():
    template_path = generate_template_csv()
    return send_file(template_path, as_attachment=True)

@app.route('/aluno', methods=['GET', 'POST'])
def aluno():
    base_url = get_secure_base_url()

    if request.method == 'POST':
        name = request.form.get('name')
        turma_id = request.form.get('turma_id')

        # ✅ Validações básicas
        if not name:
            return "Erro: Nome não pode estar vazio."
        if not turma_id:
            return "Erro: Código da turma não pode estar vazio."

        # ✅ Busca os dados da turma no Firestore
        try:
            turma_ref = db.collection("turmas").document(turma_id)
            turma_doc = turma_ref.get()

            if not turma_doc.exists:
                return f"Erro: Turma com código {turma_id} não encontrada."

            turma_data = turma_doc.to_dict()
            nome_turma = turma_data.get("nome", "Turma sem nome")
            data_evento = turma_data.get("data_evento", "Data do evento não informada")
            nome_treinamento = turma_data.get("nome_treinamento", "Treinamento não especificado")
            carga_horaria = turma_data.get("carga_horaria", "Carga horária não informada")

            logger.info(f"✅ Turma encontrada: {nome_turma} - {data_evento}")
            logger.info(f"🔎 Turma Info | Nome: {nome_turma}, Data Evento: {data_evento}, Treinamento: {nome_treinamento}, Carga Horária: {carga_horaria}")


        except Exception as e:
            logger.error(f"❌ Erro ao buscar turma {turma_id}: {e}")
            return "Erro ao buscar informações da turma."

        # ✅ Chama e captura o caminho e o código único corretamente!
        logger.info(f"🚀 Gerando certificado para {name} na turma {nome_turma} ({turma_id})")

        result = generate_certificate_for_student(
            name,
            base_url,
            nome_turma=nome_turma,
            data_evento=data_evento,
            nome_treinamento=nome_treinamento,
            carga_horaria=carga_horaria
        )

        # ✅ Se não veio nada, erro!
        if not result:
            logger.error(f"❌ Erro ao gerar o certificado para {name}")
            return "Erro ao gerar o certificado."

        certificate_path, unique_hash = result

        # ✅ Se o código veio vazio, erro!
        if not unique_hash:
            logger.error(f"❌ Código único vazio após geração de certificado para {name}")
            return "Erro ao gerar o código do certificado."

        # ✅ Monta o link de validação e compartilhamento com o código correto!
        validar_url = f"{base_url}/validar?codigo={unique_hash}"
        linkedin_share_url = f"https://www.linkedin.com/sharing/share-offsite/?url={base_url}/conquista/{unique_hash}"

        # 🔎 LOGS PARA DEBUG!
        logger.info("DEBUG INFO:")
        logger.info(f"Base URL: {base_url}")
        logger.info(f"Unique Hash: {unique_hash}")
        logger.info(f"Cert Path: {certificate_path}")
        logger.info(f"Validar URL: {validar_url}")
        logger.info(f"LinkedIn URL: {linkedin_share_url}")

        # ✅ Gera o Base64 da imagem para exibir na tela
        with open(certificate_path, "rb") as image_file:
            img_base64 = base64.b64encode(image_file.read()).decode('utf-8')

        # ✅ Retorna a página HTML com a imagem e os links
        return f'''
        <html>
        <head>
            <title>Certificado Gerado</title>
            <link rel="stylesheet" href="{base_url}/static/styles.css">
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

    # 🔵 Se for GET (fora do POST)
    return f'''
    <html>
    <head>
        <title>Emitir Certificado</title>
        <link rel="stylesheet" href="{base_url}/static/styles.css">
    </head>
    <body>
        <h1>Emitir Certificado</h1>
        <form action="/aluno" method="post">
            <label for="name">Digite seu nome:</label><br>
            <input type="text" name="name" required><br><br>

            <label for="turma_id">Digite o código da turma:</label><br>
            <input type="text" name="turma_id" required><br><br>

            <button type="submit">Gerar Certificado</button>
        </form>
    </body>
    </html>
    '''

@app.route('/upload', methods=['POST'])
def upload_file():
    base_url = get_secure_base_url()

    # ✅ Pega o arquivo e o código da turma
    uploaded_file = request.files.get('file')
    turma_id = request.form.get('turma_id')

    if not uploaded_file or not turma_id:
        logger.error("❌ CSV ou ID da turma não fornecido!")
        return "❌ Arquivo CSV e código da turma são obrigatórios!", 400

    logger.info(f"📥 Recebido arquivo CSV '{uploaded_file.filename}' para a turma {turma_id}")

    # ✅ Valida o tipo do arquivo (só pra garantir)
    if not uploaded_file.filename.endswith('.csv'):
        logger.error(f"❌ O arquivo '{uploaded_file.filename}' não é um CSV válido!")
        return "❌ Apenas arquivos CSV são aceitos!", 400

    try:
        # ✅ Salva temporariamente o arquivo no servidor
        file_path = os.path.join(UPLOAD_FOLDER, uploaded_file.filename)
        uploaded_file.save(file_path)

        logger.info(f"✅ Arquivo CSV salvo temporariamente em {file_path}")

        # ✅ Gera os certificados em lote (com a turma)
        zip_path = generate_certificates(file_path, base_url, turma_id)

        if not zip_path:
            logger.error("❌ Erro durante a geração dos certificados em lote.")
            return "❌ Erro ao gerar os certificados em lote.", 500

        logger.info(f"✅ Certificados em lote gerados e compactados! ZIP pronto para download: {zip_path}")

        # ✅ Envia o ZIP para download
        return send_file(
            zip_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name='certificados_lote.zip'
        )

    except Exception as e:
        logger.error(f"❌ Erro inesperado durante upload e geração de certificados: {e}")
        return "❌ Ocorreu um erro interno ao processar o upload e gerar os certificados.", 500


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
        logger.error("❌ Firestore não inicializado!")
        return "❌ Firestore não inicializado!", 500

    base_url = get_secure_base_url()

    codigo = None

    # 1️⃣ Se for POST (formulário enviado)
    if request.method == 'POST':
        codigo = request.form.get('codigo')

    # 2️⃣ Se for GET com parâmetro na URL
    if request.method == 'GET' and request.args.get('codigo'):
        codigo = request.args.get('codigo')

    # Se ainda não tem código, exibe o formulário
    if not codigo:
        return f'''
        <html>
        <head>
            <title>Validação de Certificado</title>
            <link rel="stylesheet" href="{base_url}/static/styles.css">
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
        logger.info(f"🔍 Validando certificado com ID: {codigo}")

        # 1️⃣ Busca o documento no Firestore
        doc_ref = db.collection("certificados").document(codigo)
        doc = doc_ref.get()

        if not doc.exists:
            logger.warning(f"❌ Documento não encontrado para o código: {codigo}")
            return f'''
            <html>
            <head>
                <title>Certificado Não Encontrado</title>
                <link rel="stylesheet" href="{base_url}/static/styles.css">
            </head>
            <body>
                <h1>❌ Certificado não encontrado!</h1>
                <p>Verifique o código e tente novamente.</p>
                <a class="back-link" href="/validar">🔙 Tentar outro código</a>
            </body>
            </html>
            ''', 404

        # 2️⃣ Recupera os dados
        data = doc.to_dict()
        nome = data.get('nome')
        data_emissao = data.get('data_emissao')

        # ⚠️ Dados adicionais
        turma_nome = data.get('turma_nome', 'Turma não informada')
        data_evento = data.get('data_evento', 'Data do evento não informada')
        nome_treinamento = data.get('nome_treinamento', 'Treinamento não especificado')
        carga_horaria = data.get('carga_horaria', 'Carga horária não informada')

        logger.info(f"✅ Certificado válido! Nome: {nome}, Turma: {turma_nome}, Evento: {data_evento}, Treinamento: {nome_treinamento}, Carga Horária: {carga_horaria}, Data emissão: {data_emissao}")

        # 3️⃣ Gerar certificado para exibir
        try:
            certificate = montar_certificado_imagem(
                nome=nome,
                data_emissao=data_emissao,
                codigo=codigo,
                base_url=base_url,
                turma_nome=turma_nome,
                data_evento=data_evento,
                nome_treinamento=nome_treinamento,
                carga_horaria=carga_horaria
            )

            img_io = io.BytesIO()
            certificate.save(img_io, 'PNG')
            img_io.seek(0)

            import base64
            img_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')

        except Exception as e:
            logger.error(f"❌ Erro ao gerar imagem do certificado para visualização: {e}")
            img_base64 = None

        # 4️⃣ Retorna a página HTML com o resultado
        return f'''
        <html>
        <head>
            <title>Validação de Certificado</title>
            <link rel="stylesheet" href="{base_url}/static/styles.css">
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
                <p><strong>Turma:</strong> {turma_nome}</p>
                <p><strong>Data do Evento:</strong> {data_evento}</p>
                <p><strong>Treinamento:</strong> {nome_treinamento}</p>
                <p><strong>Carga Horária:</strong> {carga_horaria}</p>
                <p><strong>ID de Validação:</strong> {codigo}</p>
            </div>

            {'<img class="cert-image" src="data:image/png;base64,' + img_base64 + '">' if img_base64 else '<p>Erro ao carregar a imagem do certificado.</p>'}

            <div style="margin-top: 30px;">
                <a class="back-link" href="/validar">🔙 Validar outro certificado</a>
            </div>
        </body>
        </html>
        '''

    except Exception as e:
        logger.error(f"❌ Erro inesperado na validação: {e}")
        return f'''
        <html>
        <head>
            <title>Erro na Validação</title>
            <link rel="stylesheet" href="{base_url}/static/styles.css">
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

        base_url = get_secure_base_url()
        certificate = montar_certificado_imagem(nome, data_emissao, codigo, base_url)

        if not certificate:
            print("❌ Erro ao montar o certificado.")
            return "❌ Erro ao montar o certificado.", 500

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
        logger.info(f"🔍 Iniciando download do certificado com ID: {codigo}")

        # 1️⃣ Verifica se o Firestore está inicializado
        if db is None:
            logger.error("❌ Firestore não inicializado!")
            return "❌ Erro interno: Firestore não inicializado!", 500

        # 2️⃣ Busca o certificado no Firestore pelo código único
        doc_ref = db.collection("certificados").document(codigo)
        doc = doc_ref.get()

        if not doc.exists:
            logger.warning(f"❌ Certificado com ID {codigo} não encontrado para download!")
            return "❌ Certificado não encontrado!", 404

        # 3️⃣ Recupera os dados básicos + novos campos
        data = doc.to_dict()
        nome = data.get('nome')
        data_emissao = data.get('data_emissao')

        turma_nome = data.get('turma_nome', 'Turma não informada')
        data_evento = data.get('data_evento', 'Data do evento não informada')
        nome_treinamento = data.get('nome_treinamento', 'Treinamento não especificado')
        carga_horaria = data.get('carga_horaria', 'Carga horária não informada')

        logger.info(f"✅ Dados do certificado recuperados: Nome={nome}, Data Emissão={data_emissao}, Turma={turma_nome}, Evento={data_evento}, Treinamento={nome_treinamento}, Carga Horária={carga_horaria}")

        # 4️⃣ Gera a base URL para o QR Code
        base_url = get_secure_base_url()

        # 5️⃣ Monta novamente o certificado com TODAS as informações
        certificate = montar_certificado_imagem(
            nome=nome,
            data_emissao=data_emissao,
            codigo=codigo,
            base_url=base_url,
            turma_nome=turma_nome,
            data_evento=data_evento,
            nome_treinamento=nome_treinamento,
            carga_horaria=carga_horaria
        )

        if not certificate:
            logger.error("❌ Falha ao montar o certificado para download!")
            return "❌ Erro ao gerar o certificado!", 500

        # 6️⃣ Salva o certificado em memória (buffer)
        img_io = io.BytesIO()
        certificate.save(img_io, 'PNG')
        img_io.seek(0)

        # 7️⃣ Prepara o nome do arquivo
        filename = f"{nome.replace(' ', '_')}_certificado.png"
        logger.info(f"✅ Certificado pronto para download: {filename}")

        # 8️⃣ Retorna o arquivo para o usuário
        return send_file(
            img_io,
            mimetype='image/png',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"❌ Erro ao preparar download do certificado {codigo}: {e}")
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
        base_url = get_secure_base_url()

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

    base_url = get_secure_base_url()

    if request.method == 'POST':
        nome = request.form.get('nome')
        data_evento = request.form.get('data_evento')
        nome_cliente = request.form.get('nome_cliente')
        nome_treinamento = request.form.get('nome_treinamento')
        carga_horaria = request.form.get('carga_horaria')

        # ✅ Valida se os campos obrigatórios estão preenchidos
        if not nome or not data_evento or not nome_cliente or not nome_treinamento or not carga_horaria:
            return "❌ Todos os campos são obrigatórios: Nome da Turma, Data do Evento, Nome do Cliente, Nome do Treinamento e Carga Horária."

        try:
            turma_id = str(uuid.uuid4())[:16]  # ID único da turma

            # ✅ Salva no Firestore na coleção "turmas"
            doc_ref = db.collection("turmas").document(turma_id)
            doc_ref.set({
                "id": turma_id,
                "nome": nome,
                "data_evento": data_evento,
                "nome_cliente": nome_cliente,
                "nome_treinamento": nome_treinamento,
                "carga_horaria": carga_horaria
            })

            print(f"✅ Turma criada: {nome} - {data_evento} (ID: {turma_id}) | Carga horária: {carga_horaria}")

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
                <p><strong>Cliente:</strong> {nome_cliente}</p>
                <p><strong>Treinamento:</strong> {nome_treinamento}</p>
                <p><strong>Carga Horária:</strong> {carga_horaria} horas</p>
                <p><strong>ID da Turma:</strong> {turma_id}</p>
                <br>
                <a href="/turmas/criar">➕ Criar Nova Turma</a><br>
                <a href="/turmas">📋 Ver Turmas Criadas</a><br>
                <a href="/">🔙 Voltar ao Início</a>
            </body>
            </html>
            '''

        except Exception as e:
            print(f"❌ Erro ao criar turma: {e}")
            return f"❌ Erro ao criar turma: {e}", 500

    # Se for GET, exibe o formulário com o novo campo de carga horária
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

            <label for="nome_cliente">Nome do Cliente:</label><br>
            <input type="text" id="nome_cliente" name="nome_cliente" required><br><br>

            <label for="nome_treinamento">Nome do Treinamento:</label><br>
            <input type="text" id="nome_treinamento" name="nome_treinamento" required><br><br>

            <label for="carga_horaria">Carga Horária (horas):</label><br>
            <input type="number" id="carga_horaria" name="carga_horaria" min="1" required><br><br>

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
                "data_evento": data.get('data_evento'),
                "nome_cliente": data.get('nome_cliente'),
                "nome_treinamento": data.get('nome_treinamento'),
                "carga_horaria": data.get('carga_horaria', 'Não informado')  # ✅ Campo novo
            })

        # Ordena pelo nome da turma (opcional)
        turmas.sort(key=lambda x: x['nome'])

        base_url = get_secure_base_url()

        # Monta as linhas da tabela
        table_rows = ""
        for turma in turmas:
            table_rows += f"""
                <tr>
                    <td>{turma['id']}</td>
                    <td>{turma['nome']}</td>
                    <td>{turma['data_evento']}</td>
                    <td>{turma['nome_cliente']}</td>
                    <td>{turma['nome_treinamento']}</td>
                    <td>{turma['carga_horaria']} horas</td>
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
            <h1>📋 Lista de Turmas Cadastradas</h1>
            <table>
                <tr>
                    <th>ID da Turma</th>
                    <th>Nome da Turma</th>
                    <th>Data do Evento</th>
                    <th>Cliente</th>
                    <th>Treinamento</th>
                    <th>Carga Horária</th> <!-- ✅ Nova coluna -->
                </tr>
                {table_rows}
            </table>
            <br>
            <a class="back-link" href="/turmas/criar">➕ Criar Nova Turma</a><br>
            <a class="back-link" href="/">🔙 Voltar ao Início</a>
        </body>
        </html>
        '''

    except Exception as e:
        print(f"❌ Erro ao listar turmas: {e}")
        return f"❌ Erro ao listar turmas: {e}", 500


@app.route('/conquista/<codigo>')
def conquista(codigo):
    global db

    logger.info(f"🔍 Acessando página de conquista do certificado {codigo}")

    # 1️⃣ Busca o certificado no Firestore
    doc_ref = db.collection("certificados").document(codigo)
    doc = doc_ref.get()

    if not doc.exists:
        logger.warning(f"❌ Certificado não encontrado: {codigo}")
        return "❌ Certificado não encontrado!", 404

    # 2️⃣ Recupera todos os dados necessários
    data = doc.to_dict()

    nome = data.get('nome')
    data_emissao = data.get('data_emissao')
    turma_nome = data.get('turma_nome', "Turma não especificada")
    data_evento = data.get('data_evento', "Data do evento não informada")
    nome_treinamento = data.get('nome_treinamento', "Treinamento não especificado")
    carga_horaria = data.get('carga_horaria', "Carga horária não informada")

    logger.info(f"✅ Dados do certificado recuperados para a conquista:")
    logger.info(f"Nome: {nome} | Emissão: {data_emissao} | Turma: {turma_nome} | Evento: {data_evento} | Treinamento: {nome_treinamento} | Carga horária: {carga_horaria}")

    # 3️⃣ Informações para o Open Graph (LinkedIn e redes)
    base_url = get_secure_base_url()

    image_url = f"{base_url}/download_cert/{codigo}"

    # 4️⃣ Título e descrição para redes sociais com mais informações
    titulo = f"{nome} conquistou seu certificado no treinamento {nome_treinamento}!"
    descricao = (
        f"Participou da turma '{turma_nome}', no evento de {data_evento}, com carga horária de {carga_horaria}h. "
        f"Recebeu seu certificado em {data_emissao}. Confira!"
    )

    # 5️⃣ Página HTML com Open Graph + exibição de informações detalhadas
    return f'''
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>{titulo}</title>

        <!-- Link para o CSS existente -->
        <link rel="stylesheet" href="{base_url}/static/styles.css">

        <!-- Open Graph Tags -->
        <meta property="og:title" content="{titulo}" />
        <meta property="og:description" content="{descricao}" />
        <meta property="og:image" content="{image_url}" />
        <meta property="og:type" content="website" />
        <meta property="og:url" content="{base_url}/conquista/{codigo}" />

        <!-- SEO -->
        <meta name="description" content="{descricao}">
        <meta name="robots" content="index, follow">

        <!-- Cache-control para debug -->
        <meta http-equiv="cache-control" content="no-cache" />
        <meta http-equiv="pragma" content="no-cache" />
    </head>
    <body>
        <div class="container">
            <h1>🎉 {titulo}</h1>
            <p>{descricao}</p>

            <img src="{image_url}" alt="Certificado de {nome}" style="width:100%; max-width:600px; margin: 20px auto; border-radius: 10px;">

            <div class="details-section" style="margin-top: 30px;">
                <h2>📄 Detalhes do Certificado</h2>
                <ul style="list-style-type:none;">
                    <li><strong>Nome do Participante:</strong> {nome}</li>
                    <li><strong>Treinamento:</strong> {nome_treinamento}</li>
                    <li><strong>Turma:</strong> {turma_nome}</li>
                    <li><strong>Data do Evento:</strong> {data_evento}</li>
                    <li><strong>Carga Horária:</strong> {carga_horaria}</li>
                    <li><strong>Data de Emissão:</strong> {data_emissao}</li>
                    <li><strong>ID de Validação:</strong> {codigo}</li>
                </ul>
            </div>

            <div class="cta-section" style="margin-top: 30px;">
                <h2>🚀 Faça parte da mudança!</h2>
                <p>Descubra como melhorar sua relação com a tecnologia.</p>
                <a class="btn" href="https://www.equilibrionline.com.br/tdi/" target="_blank">Fazer TDI</a>
                <a class="btn" href="https://www.equilibrionline.com.br/tdi-paisefilhos/" target="_blank">Fazer TDIPF</a>
            </div>

            <div class="cta-section" style="margin-top: 30px;">
                <h2>💼 Quer levar isso para sua empresa?</h2>
                <p>Aumente a produtividade e a saúde mental da sua equipe com o EquilibriON!</p>
                <a class="btn" href="https://www.equilibrionline.com.br/solucoes-para-empresas/" target="_blank">Conheça nossos serviços</a>
            </div>

            <div style="margin-top: 30px;">
                <a class="link" href="{base_url}/validar?codigo={codigo}">🔎 Validar Certificado</a><br>
                <a class="link" href="/">🔙 Voltar ao Início</a>
            </div>
        </div>
    </body>
    </html>
    '''
@app.errorhandler(404)
def page_not_found(e):
    base_url = get_secure_base_url()  # já existe no seu código!
    
    return f'''
    <html>
    <head>
        <title>Página não encontrada</title>
        <link rel="stylesheet" href="{base_url}/static/styles.css">
    </head>
    <body>
        <h1>❌ Página não encontrada</h1>
        <p>A rota que você tentou acessar não existe ou foi removida.</p>
        
        <div style="margin-top: 20px;">
            <a class="btn" href="{base_url}">🔙 Voltar para o início</a>
        </div>
    </body>
    </html>
    ''', 404



if __name__ == '__main__':
    print("Rotas disponíveis:")
    for rule in app.url_map.iter_rules():
        print(rule)
    app.run(host='0.0.0.0', port=8080, threaded=True)