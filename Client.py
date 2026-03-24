import socketio
import rsa

# 1. Configuração do Cliente e Chaves
sio = socketio.Client()
print("Gerando chaves RSA...")
(pub_key, priv_key) = rsa.newkeys(1024)
pub_pem = pub_key.save_pkcs1() # Transforma chave em formato enviável

# 2. Eventos do Chat
@sio.on('message')
def on_message(data):
    try:
        # Descriptografa a mensagem recebida com sua chave privada
        texto = rsa.decrypt(data, priv_key).decode('utf-8')
        print(f"\n[Amigo]: {texto}")
    except:
        print("\n[Erro]: Recebi uma mensagem que não foi trancada para mim.")

@sio.on('receber_chave')
def on_key(data):
    global chave_do_amigo
    chave_do_amigo = rsa.PublicKey.load_pkcs1(data)
    print("\n[Sistema]: Chave pública do amigo recebida!")

# 3. Conexão
URL_SERVER = 'http://127.0.0.1:5000' # Troque pela URL do Render depois!
sio.connect(URL_SERVER)
sio.emit('enviar_chave', pub_pem)

while True:
    msg = input("Você: ")
    try:
        # Tranca a mensagem com a chave do amigo
        msg_cifrada = rsa.encrypt(msg.encode('utf-8'), chave_do_amigo)
        sio.emit('message', msg_cifrada)
    except NameError:
        print("Aguardando o amigo entrar para trocar chaves...")

