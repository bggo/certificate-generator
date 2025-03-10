
# Usando uma imagem base do Python
FROM python:3.11

# Configurar o ambiente de trabalho
WORKDIR /app

# Instalar dependências do sistema (incluindo suporte a localidade pt_BR)
RUN apt-get update && apt-get install -y locales && \
    sed -i '/pt_BR.UTF-8/s/^# //' /etc/locale.gen && \
    locale-gen

# Definir a localidade padrão do sistema como português do Brasil
ENV LANG=pt_BR.UTF-8
ENV LANGUAGE=pt_BR:pt
ENV LC_ALL=pt_BR.UTF-8

# Copiar arquivos do projeto para o contêiner
COPY . .

# Instalar dependências do Python
RUN pip install flask pillow google-cloud-firestore google-cloud-secret-manager qrcode
# Expor a porta para o Cloud Run
EXPOSE 8080

# Comando para iniciar a aplicação
CMD ["python", "app.py"]