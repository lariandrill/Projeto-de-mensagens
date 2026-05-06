import sqlite3
import threading
import rsa
import os
import sys
import socketio
import hashlib
import json
import requests
from kivy.uix.widget import Widget
from kivy.uix.image import Image
from kivy.core.image import Image as CoreImage
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.screenmanager import ScreenManager, Screen, NoTransition
from kivy.uix.checkbox import CheckBox
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle, Ellipse, Rectangle
from kivy.metrics import dp
from kivy.core.window import Window
import base64
from datetime import datetime
import time

try:
    from plyer import notification
    NOTIFICACOES_DISPONIVEIS = True
except ImportError:
    NOTIFICACOES_DISPONIVEIS = False

config_file = "config.json"
configuracoes = {
    "notificacoes": True,
    "confirmacao_leitura": True,
    "som_ao_receber": False,
    "som_ao_enviar": False
}

def carregar_configuracoes():
    global configuracoes
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                for k, v in configuracoes.items():
                    config.setdefault(k, v)
                configuracoes.update(config)
        except:
            pass

def salvar_configuracoes():
    with open(config_file, 'w') as f:
        json.dump(configuracoes, f, indent=2)

carregar_configuracoes()

URL_RENDER = "https://projeto-de-mensagens.onrender.com"

sio = socketio.Client()

chaves_amigos = {}
USUARIO_LOGADO = None
priv_key = None
pub_key = None
destinatario_atual = None
tentando_reconectar = False
usuarios_online = []
todos_contatos = []
mensagens_pendentes_confirmacao = {}
digito_temporizador = None

def emitir_digitando():
    global digito_temporizador
    if destinatario_atual:
        sio.emit('digitando', {'to': destinatario_atual, 'from': USUARIO_LOGADO})
    if digito_temporizador:
        Clock.unschedule(digito_temporizador)
    digito_temporizador = Clock.schedule_once(lambda dt: None, 2)

def get_path():
    if os.name == 'posix':
        return os.path.join(App.get_running_app().user_data_dir, 'conversas.db')
    return 'conversas.db'

def init_chat_db():
    conn = sqlite3.connect(get_path())
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS mensagens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT NOT NULL,
        destinatario TEXT NOT NULL,
        mensagem TEXT NOT NULL,
        tipo TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        lida INTEGER DEFAULT 0,
        entregue INTEGER DEFAULT 0
    )''')
    try:
        c.execute('ALTER TABLE mensagens ADD COLUMN entregue INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    c.execute('''CREATE TABLE IF NOT EXISTS ultimas_conversas (
        usuario TEXT NOT NULL,
        contato TEXT NOT NULL,
        ultima_mensagem TEXT,
        ultimo_timestamp DATETIME,
        PRIMARY KEY (usuario, contato)
    )''')
    conn.commit()
    conn.close()

def salvar_mensagem(usuario, destinatario, mensagem, tipo):
    try:
        conn = sqlite3.connect(get_path())
        c = conn.cursor()
        c.execute('''INSERT INTO mensagens (usuario, destinatario, mensagem, tipo, timestamp, entregue) 
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (usuario, destinatario, mensagem, tipo, datetime.now(), 0 if tipo == 'enviada' else 1))
        c.execute('''INSERT OR REPLACE INTO ultimas_conversas 
                     (usuario, contato, ultima_mensagem, ultimo_timestamp)
                     VALUES (?, ?, ?, ?)''',
                  (usuario, destinatario, mensagem, datetime.now()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERRO] Ao salvar mensagem: {e}")
        return False

def carregar_historico(usuario, contato, limit=100):
    try:
        conn = sqlite3.connect(get_path())
        c = conn.cursor()
        c.execute('''SELECT mensagem, tipo, timestamp, entregue 
                     FROM mensagens 
                     WHERE (usuario = ? AND destinatario = ?)
                        OR (usuario = ? AND destinatario = ?)
                     ORDER BY timestamp ASC LIMIT ?''',
                  (usuario, contato, contato, usuario, limit))
        mensagens = c.fetchall()
        conn.close()
        return mensagens
    except Exception as e:
        print(f"[ERRO] Ao carregar histórico: {e}")
        return []

def marcar_mensagens_lidas(usuario, contato):
    try:
        conn = sqlite3.connect(get_path())
        c = conn.cursor()
        c.execute('''UPDATE mensagens 
                     SET lida = 1 
                     WHERE usuario = ? AND destinatario = ? AND tipo = 'recebida' AND lida = 0''',
                  (contato, usuario))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERRO] Ao marcar mensagens: {e}")

def contar_mensagens_nao_lidas(usuario):
    try:
        conn = sqlite3.connect(get_path())
        c = conn.cursor()
        c.execute('''SELECT usuario, COUNT(*) 
                     FROM mensagens 
                     WHERE destinatario = ? AND tipo = 'recebida' AND lida = 0
                     GROUP BY usuario''',
                  (usuario,))
        result = c.fetchall()
        conn.close()
        return {contato: count for contato, count in result}
    except Exception as e:
        print(f"[ERRO] Ao contar mensagens: {e}")
        return {}

def listar_contatos_com_conversa(usuario):
    try:
        conn = sqlite3.connect(get_path())
        c = conn.cursor()
        c.execute('''SELECT contato, ultima_mensagem, ultimo_timestamp 
                     FROM ultimas_conversas 
                     WHERE usuario = ?
                     ORDER BY ultimo_timestamp DESC''',
                  (usuario,))
        result = c.fetchall()
        conn.close()
        return result
    except Exception as e:
        print(f"[ERRO] Ao listar contatos: {e}")
        return []

def contar_mensagens_nao_entregues(usuario):
    try:
        conn = sqlite3.connect(get_path())
        c = conn.cursor()
        c.execute('''SELECT destinatario, COUNT(*) 
                     FROM mensagens 
                     WHERE usuario = ? AND tipo = 'enviada' AND entregue = 0
                     GROUP BY destinatario''',
                  (usuario,))
        result = c.fetchall()
        conn.close()
        return {destinatario: count for destinatario, count in result}
    except Exception as e:
        print(f"[ERRO] Ao contar mensagens nao entregues: {e}")
        return {}

def pesquisar_no_historico(usuario, contato, termo):
    try:
        conn = sqlite3.connect(get_path())
        c = conn.cursor()
        c.execute('''SELECT mensagem, tipo, timestamp 
                     FROM mensagens 
                     WHERE (usuario = ? AND destinatario = ? OR usuario = ? AND destinatario = ?)
                     AND mensagem LIKE ?
                     ORDER BY timestamp ASC''',
                  (usuario, contato, contato, usuario, f'%{termo}%'))
        resultados = c.fetchall()
        conn.close()
        return resultados
    except Exception as e:
        print(f"[ERRO] Pesquisa: {e}")
        return []

def tentar_conectar_loop(dt=None):
    global tentando_reconectar
    if not sio.connected and not tentando_reconectar:
        try:
            tentando_reconectar = True
            print(f"[INFO] Tentando conectar em {URL_RENDER}...")
            sio.connect(URL_RENDER, transports=['websocket', 'polling'])
            tentando_reconectar = False
        except Exception as e:
            print(f"[ERRO] Falha na tentativa: {e}")
            tentando_reconectar = False
            Clock.schedule_once(tentar_conectar_loop, 10)

def conectar_servidor():
    try:
        if not sio.connected:
            sio.connect(URL_RENDER, transports=['websocket', 'polling'])
            print("[OK] Conectado com sucesso!")
    except Exception as e:
        print(f"[ERRO] Erro ao conectar: {e}")
        Clock.schedule_once(lambda dt: tentar_conectar_loop(), 5)

@sio.event
def connect():
    print("[OK] CONECTADO AO SERVIDOR!")
    try:
        Clock.unschedule(tentar_conectar_loop)
    except:
        pass

    app = App.get_running_app()
    if app and app.root:
        if 'chat' in app.root.screen_names:
            chat_screen = app.root.get_screen('chat')
            if hasattr(chat_screen, 'atualizar_status'):
                Clock.schedule_once(lambda dt: chat_screen.atualizar_status(True))
        if 'login' in app.root.screen_names:
            login_screen = app.root.get_screen('login')
            if hasattr(login_screen, 'atualizar_status_socket'):
                Clock.schedule_once(lambda dt: login_screen.atualizar_status_socket(True))

@sio.event
def connect_error(data):
    print(f"[ERRO] Conexao: {data}")
    app = App.get_running_app()
    if app and app.root and 'login' in app.root.screen_names:
        login_screen = app.root.get_screen('login')
        if hasattr(login_screen, 'atualizar_status_socket'):
            Clock.schedule_once(lambda dt: login_screen.atualizar_status_socket(False))
    Clock.schedule_once(tentar_conectar_loop, 10)

@sio.event
def disconnect():
    print("[AVISO] Desconectado do servidor!")
    app = App.get_running_app()
    if app and app.root:
        if 'chat' in app.root.screen_names:
            chat_screen = app.root.get_screen('chat')
            if hasattr(chat_screen, 'atualizar_status'):
                Clock.schedule_once(lambda dt: chat_screen.atualizar_status(False))
        if 'login' in app.root.screen_names:
            login_screen = app.root.get_screen('login')
            if hasattr(login_screen, 'atualizar_status_socket'):
                Clock.schedule_once(lambda dt: login_screen.atualizar_status_socket(False))
    Clock.schedule_once(tentar_conectar_loop, 5)

@sio.on('delivery_confirmation')
def on_delivery_confirmation(data):
    to = data.get('to')
    from_user = data.get('from')
    status = data.get('status')
    app = App.get_running_app()
    if app and hasattr(app, 'log_na_tela') and configuracoes["confirmacao_leitura"]:
        if status == 'delivered':
            Clock.schedule_once(lambda dt: app.log_na_tela(f"[OK] Mensagem entregue para {to}", (0, 0.5, 0, 0.6), 'center'))
            print(f"[ENTREGUE] Mensagem para {to} entregue")
        elif status == 'stored_offline':
            Clock.schedule_once(lambda dt: app.log_na_tela(f"[OFFLINE] Mensagem armazenada para {to} (entregue quando online)", (0.5, 0.5, 0, 0.6), 'center'))
            print(f"[ARMAZENADA] Mensagem para {to} armazenada no servidor")

@sio.on('digitando')
def on_digitando(data):
    if destinatario_atual == data.get('from'):
        app = App.get_running_app()
        if app and hasattr(app, 'mostrar_indicador_digitacao'):
            Clock.schedule_once(lambda dt: app.mostrar_indicador_digitacao(data.get('from')), 0)

@sio.on('chave_usuario')
def receber_chave_usuario(data):
    global chaves_amigos, usuarios_online
    try:
        print(f"[CHAVE] Recebendo: {data}")
        if isinstance(data, dict):
            username = data.get('username')
            chave_base64 = data.get('public_key')
            if username and chave_base64 and username != USUARIO_LOGADO:
                chave_bytes = base64.b64decode(chave_base64)
                chave_publica = rsa.PublicKey.load_pkcs1(chave_bytes)
                chaves_amigos[username] = chave_publica
                if username not in usuarios_online:
                    usuarios_online.append(username)
                app = App.get_running_app()
                if app and hasattr(app, 'log_na_tela'):
                    Clock.schedule_once(lambda dt: app.log_na_tela(f"[ONLINE] {username} esta online!", (0, 0.5, 0, 0.8), 'center'))
                print(f"[OK] Chave de {username} armazenada. Total: {len(chaves_amigos)}")
                if app and hasattr(app, 'atualizar_indicador_nao_lidas'):
                    Clock.schedule_once(lambda dt: app.atualizar_indicador_nao_lidas())
    except Exception as e:
        print(f"[ERRO] Ao receber chave: {e}")

@sio.on('lista_contatos')
def receber_lista_contatos(contatos):
    global todos_contatos, chaves_amigos, usuarios_online
    try:
        print(f"[LISTA] Contatos recebidos: {contatos}")
        todos_contatos = contatos
        usuarios_online = [c['username'] for c in contatos if c['online']]
        chaves_amigos = {}
        for c in contatos:
            if c['online'] and c.get('public_key'):
                chave_bytes = base64.b64decode(c['public_key'])
                chave_publica = rsa.PublicKey.load_pkcs1(chave_bytes)
                chaves_amigos[c['username']] = chave_publica
        app = App.get_running_app()
        if app and hasattr(app, 'atualizar_indicador_nao_lidas'):
            Clock.schedule_once(lambda dt: app.atualizar_indicador_nao_lidas())
        if app and hasattr(app, 'log_na_tela'):
            Clock.schedule_once(lambda dt: app.log_na_tela(
                f"[ONLINE] {len(usuarios_online)} de {len(contatos)} usuarios online",
                (0.3, 0.3, 0.5, 1), 'center'
            ))
    except Exception as e:
        print(f"[ERRO] Ao processar contatos: {e}")

@sio.on('message')
def on_message(data):
    try:
        global priv_key
        app = App.get_running_app()
        print(f"[MSG] Recebida: {data}")
        if isinstance(data, dict):
            remetente = data.get('from')
            conteudo = data.get('content')
            is_offline = data.get('offline', False)
            if remetente and conteudo and priv_key:
                try:
                    try:
                        msg_bytes = base64.b64decode(conteudo)
                        msg_decifrada = rsa.decrypt(msg_bytes, priv_key).decode()
                        print(f"[OK] Mensagem descriptografada de {remetente}")
                    except:
                        msg_decifrada = conteudo
                        print(f"[OK] Mensagem em texto puro de {remetente}")
                    salvar_mensagem(remetente, USUARIO_LOGADO, msg_decifrada, 'recebida')
                    if destinatario_atual == remetente:
                        if app and hasattr(app, 'log_na_tela'):
                            Clock.schedule_once(lambda dt: app.log_na_tela(f"[{remetente}]: {msg_decifrada}", (0.2, 0.2, 0.25, 1), 'left'))
                        marcar_mensagens_lidas(USUARIO_LOGADO, remetente)
                        if app and hasattr(app, 'cancelar_indicador_digitacao'):
                            app.cancelar_indicador_digitacao()
                    else:
                        if app and hasattr(app, 'atualizar_indicador_nao_lidas'):
                            Clock.schedule_once(lambda dt: app.atualizar_indicador_nao_lidas())
                        if app and hasattr(app, 'log_na_tela'):
                            if is_offline:
                                Clock.schedule_once(lambda dt: app.log_na_tela(f"[OFFLINE] Mensagem de {remetente} entregue", (0.5, 0.5, 0, 0.8), 'center'))
                            else:
                                Clock.schedule_once(lambda dt: app.log_na_tela(f"[NOVA] Mensagem de {remetente}", (0.7, 0.5, 0, 0.8), 'center'))

                        # --- Som de notificação (usando som do sistema) ---
                        if configuracoes.get("som_ao_receber", False):
                            Clock.schedule_once(lambda dt: _tocar_som_notificacao(), 0)

                        # --- Notificação visual do sistema (plyer) ---
                        if NOTIFICACOES_DISPONIVEIS and (not app.root or app.root.current != 'chat'):
                            Clock.schedule_once(lambda dt, u=remetente, m=msg_decifrada: notification.notify(
                                title=u,
                                message=m[:50],
                                app_name="HERMES",
                                timeout=5
                            ), 0)
                except Exception as e:
                    print(f"[ERRO] Ao processar mensagem: {e}")
    except Exception as e:
        print(f"[ERRO] Geral na mensagem: {e}")

def _tocar_som_notificacao():
    """Toca o som de notificação padrão do sistema operacional."""
    try:
        import platform
        import subprocess

        sistema = platform.system()

        if sistema == "Windows":
            import winsound
            winsound.PlaySound("SystemQuestion", winsound.SND_ALIAS)
        elif sistema == "Darwin":   # macOS
            subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"])
        elif sistema == "Linux":
            subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/message.ogg"])
        else:
            print("[INFO] Som de notificação não disponível para este sistema.")
    except Exception as e:
        print(f"[ERRO] Ao tocar som de notificação do sistema: {e}")

@sio.on('historico_mensagens')
def on_historico_mensagens(data):
    contato = data.get('contato')
    mensagens = data.get('mensagens', [])
    print(f"[HISTORICO] {len(mensagens)} mensagens recebidas de {contato}")
    app = App.get_running_app()
    if app and app.root.current == 'chat' and destinatario_atual == contato:
        def adicionar_historico(dt):
            chat_content = app.root.get_screen('chat').layout_principal
            chat_content.chat_container.clear_widgets()
            for msg in mensagens:
                de = msg['from']
                texto = msg['content']
                if de == USUARIO_LOGADO:
                    chat_content.adicionar_mensagem(f"[Voce]: {texto}", 'right', (0.05, 0.4, 0.2, 1))
                else:
                    chat_content.adicionar_mensagem(f"[{de}]: {texto}", 'left', (0.2, 0.2, 0.25, 1))
            sio.emit('marcar_lida', {'contato': contato})
        Clock.schedule_once(adicionar_historico, 0)

@sio.on('registro_response')
def on_registro_response(data):
    sucesso = data.get('success', False)
    mensagem = data.get('message', '')
    if sucesso:
        Clock.schedule_once(lambda dt: Toast("Conta criada! Faca login.", cor=(0,0.5,0,0.9)).open())
        Clock.schedule_once(lambda dt: setattr(App.get_running_app().root, 'current', 'login'))
    else:
        Clock.schedule_once(lambda dt: Toast(f"Erro: {mensagem}", cor=(0.7,0,0,0.9)).open())

@sio.on('login_response')
def on_login_response(data):
    sucesso = data.get('success', False)
    mensagem = data.get('message', '')
    if sucesso:
        global USUARIO_LOGADO, priv_key, pub_key, chaves_amigos, usuarios_online
        USUARIO_LOGADO = data.get('username')
        print(f"[INFO] Login bem-sucedido como {USUARIO_LOGADO}")

        def gerar_chaves_e_registrar():
            global priv_key, pub_key
            print("[INFO] Gerando chaves RSA...")
            (pub_key, priv_key) = rsa.newkeys(1024)
            pub_pem = pub_key.save_pkcs1()
            pub_base64 = base64.b64encode(pub_pem).decode('utf-8')
            sio.emit('registrar_usuario', {'username': USUARIO_LOGADO, 'public_key': pub_base64})
            time.sleep(0.5)
            sio.emit('solicitar_contatos')
            Clock.schedule_once(lambda dt: setattr(App.get_running_app().root, 'current', 'chat'), 1.5)

        threading.Thread(target=gerar_chaves_e_registrar, daemon=True).start()
        Clock.schedule_once(lambda dt: Toast("Preparando ambiente...", cor=(0, 0.5, 0.8, 0.9)).open())
    else:
        def update_error(dt):
            app = App.get_running_app()
            login_screen = app.root.get_screen('login')
            login_screen.error_label.text = mensagem
        Clock.schedule_once(update_error)

@sio.on('*')
def catch_all(event, data):
    print(f"[DEBUG] Evento nao tratado: {event} -> {type(data)}")

def gerar_hash(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

class BotaoRedondo(Button):
    def __init__(self, cor_fundo, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_color = (0,0,0,0)
        with self.canvas.before:
            Color(*cor_fundo)
            self.rect = RoundedRectangle(radius=[dp(20)])
        self.bind(pos=self._update_rect, size=self._update_rect)
    def _update_rect(self, *args): 
        self.rect.pos = self.pos
        self.rect.size = self.size

class BalaoMensagem(Label):
    def __init__(self, text, cor_fundo, **kwargs):
        super().__init__(text=text, **kwargs)
        self.padding = (dp(15), dp(10))
        self.size_hint = (None, None)
        self.markup = True
        with self.canvas.before:
            Color(*cor_fundo)
            self.rect = RoundedRectangle(radius=[dp(15)])
        self.bind(pos=self._update_rect, size=self._update_rect, texture_size=self._update_size)

    def _update_size(self, *args):
        largura_max = App.get_running_app().root.width * 0.75 if App.get_running_app().root else dp(250)
        self.width = min(self.texture_size[0] + dp(30), largura_max)
        self.height = self.texture_size[1] + dp(20)
        self.text_size = (self.width - dp(30), None)

    def _update_rect(self, *args): 
        self.rect.pos = self.pos
        self.rect.size = self.size

class Toast(Popup):
    def __init__(self, mensagem, cor=(0.2, 0.2, 0.2, 0.9), **kwargs):
        super().__init__(title="", separator_height=0, size_hint=(0.8, 0.08), 
                         pos_hint={'center_x': 0.5, 'y': 0.05}, **kwargs)
        self.background_color = (0, 0, 0, 0)
        with self.canvas.before:
            Color(*cor)
            self.rect = RoundedRectangle(radius=[dp(10)])
        self.bind(pos=self._update_rect, size=self._update_rect)
        self.add_widget(Label(text=mensagem, bold=True, font_size='13sp'))
        Clock.schedule_once(self.dismiss, 10)

    def _update_rect(self, *args): 
        self.rect.pos = self.pos
        self.rect.size = self.size

class PopupConfirmarAcao(Popup):
    def __init__(self, titulo, callback, **kwargs):
        super().__init__(title=titulo, size_hint=(0.6, 0.5), **kwargs)
        self.callback = callback
        layout = BoxLayout(orientation='vertical', padding=dp(15), spacing=dp(10))
        if titulo == "Excluir Conta":
            msg = "[b]ESTA ACAO E IRREVERSIVEL![/b]\nTem certeza?"
            cor_aviso = (1, 0.3, 0.3, 1)
        else:
            msg = "Confirme sua [b]SENHA ATUAL[/b]:"
            cor_aviso = (1, 1, 1, 1)
        layout.add_widget(Label(text=msg, markup=True, color=cor_aviso, font_size='12sp', halign='center'))
        self.pw = TextInput(password=True, multiline=False, size_hint_y=None, height=dp(40), hint_text="Senha Atual")
        layout.add_widget(self.pw)
        btn_layout = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(45))
        btn_cancelar = BotaoRedondo(text="CANCELAR", cor_fundo=(0.3, 0.3, 0.3, 1))
        btn_cancelar.bind(on_release=self.dismiss)
        cor_btn = (0.7, 0.2, 0.2, 1) if titulo == "Excluir Conta" else (0.1, 0.5, 0.3, 1)
        btn_confirmar = BotaoRedondo(text="CONFIRMAR", cor_fundo=cor_btn)
        btn_confirmar.bind(on_release=self.validar)
        btn_layout.add_widget(btn_cancelar)
        btn_layout.add_widget(btn_confirmar)
        layout.add_widget(btn_layout)
        self.content = layout

    def validar(self, instance):
        senha_h = gerar_hash(self.pw.text)
        Toast("Funcionalidade disponivel em breve.", cor=(0.7,0,0,0.9)).open()
        self.dismiss()

class ConfiguracoesPopup(Popup):
    def __init__(self, **kwargs):
        super().__init__(title="Configuracoes", size_hint=(0.8, 0.6), **kwargs)
        layout = BoxLayout(orientation='vertical', spacing=dp(15), padding=dp(20))

        # Notificações visuais
        notif_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        notif_layout.add_widget(Label(text="Notificacoes:", size_hint_x=0.5, halign='right'))
        self.notif_check = CheckBox(active=configuracoes["notificacoes"], size_hint_x=0.1)
        self.notif_check.bind(active=self.mudar_notificacoes)
        notif_layout.add_widget(self.notif_check)
        layout.add_widget(notif_layout)

        # Som ao receber
        som_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        som_layout.add_widget(Label(text="Som ao receber:", size_hint_x=0.5, halign='right'))
        self.som_check = CheckBox(active=configuracoes.get("som_ao_receber", False), size_hint_x=0.1)
        self.som_check.bind(active=self.mudar_som_receber)
        som_layout.add_widget(self.som_check)
        layout.add_widget(som_layout)

        # Confirm. de leitura
        conf_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        conf_layout.add_widget(Label(text="Confirm. de leitura:", size_hint_x=0.5, halign='right'))
        self.conf_check = CheckBox(active=configuracoes["confirmacao_leitura"], size_hint_x=0.1)
        self.conf_check.bind(active=self.mudar_conf_leitura)
        conf_layout.add_widget(self.conf_check)
        layout.add_widget(conf_layout)

        pesquisa_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        pesquisa_layout.add_widget(Label(text="Pesquisar no historico:", size_hint_x=0.5, halign='right'))
        btn_pesquisar = BotaoRedondo(text="BUSCAR", cor_fundo=(0.2, 0.5, 0.6, 1), size_hint_x=0.3)
        btn_pesquisar.bind(on_release=self.abrir_pesquisa)
        pesquisa_layout.add_widget(btn_pesquisar)
        layout.add_widget(pesquisa_layout)

        btn_fechar = BotaoRedondo(text="FECHAR", cor_fundo=(0.5, 0.2, 0.2, 1), size_hint_y=None, height=dp(50))
        btn_fechar.bind(on_release=self.dismiss)
        layout.add_widget(btn_fechar)
        self.content = layout

    def mudar_notificacoes(self, instance, value):
        configuracoes["notificacoes"] = value
        salvar_configuracoes()

    def mudar_som_receber(self, instance, value):
        configuracoes["som_ao_receber"] = value
        salvar_configuracoes()

    def mudar_conf_leitura(self, instance, value):
        configuracoes["confirmacao_leitura"] = value
        salvar_configuracoes()

    def abrir_pesquisa(self, instance):
        self.dismiss()
        if not destinatario_atual:
            Toast("Selecione um destinatario primeiro!").open()
            return
        popup = Popup(title="Pesquisar no historico", size_hint=(0.9, 0.6))
        layout = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        input_pesquisa = TextInput(hint_text="Digite o termo...", multiline=False,
                                   background_color=(0.12,0.12,0.12,1), foreground_color=(1,1,1,1))
        btn_buscar = BotaoRedondo(text="BUSCAR", cor_fundo=(0.2,0.5,0.6,1), size_hint_y=None, height=dp(45))
        resultado_label = Label(text="", markup=True, size_hint_y=None)
        resultado_label.bind(texture_size=resultado_label.setter('size'))
        scroll = ScrollView()
        scroll.add_widget(resultado_label)
        def pesquisar(ev):
            termo = input_pesquisa.text.strip()
            if not termo:
                return
            resultados = pesquisar_no_historico(USUARIO_LOGADO, destinatario_atual, termo)
            if not resultados:
                resultado_label.text = "Nenhum resultado encontrado."
            else:
                texto = "\n".join([f"[{tipo} - {ts[:16]}]: {msg}" for msg, tipo, ts in resultados])
                resultado_label.text = texto
            scroll.scroll_y = 1
        btn_buscar.bind(on_release=pesquisar)
        layout.add_widget(input_pesquisa)
        layout.add_widget(btn_buscar)
        layout.add_widget(scroll)
        btn_fechar = BotaoRedondo(text="FECHAR", cor_fundo=(0.5,0.2,0.2,1), size_hint_y=None, height=dp(40))
        btn_fechar.bind(on_release=popup.dismiss)
        layout.add_widget(btn_fechar)
        popup.content = layout
        popup.open()

# ---------- Menu com confirmação de limpeza de histórico ----------
class MenuOpcoes(Popup):
    def __init__(self, chat_instance, **kwargs):
        super().__init__(title="Menu", size_hint=(0.45, 0.85), **kwargs)
        self.chat_ui = chat_instance
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))

        qtd_online = len(usuarios_online)
        nao_entregues = contar_mensagens_nao_entregues(USUARIO_LOGADO)
        total_pendentes = sum(nao_entregues.values())
        status_text = f"[ONLINE] {qtd_online} usuario(s) online"
        if total_pendentes > 0:
            status_text += f"\n[PENDENTE] {total_pendentes} mensagem(ns) aguardando entrega"
        if destinatario_atual:
            status_text += f"\n[CONVERSANDO] {destinatario_atual}"
            if destinatario_atual in chaves_amigos:
                status_text += " (E2EE ATIVA)"
            else:
                status_text += " (AGUARDANDO CHAVE)"
        layout.add_widget(Label(text=status_text, color=(0.7,0.7,0.7,1), 
                                font_size='11sp', size_hint_y=None, height=dp(80)))

        if destinatario_atual:
            btn_limpar = BotaoRedondo(text=f"LIMPAR HISTORICO COM {destinatario_atual}", 
                                      cor_fundo=(0.7, 0.5, 0, 1), 
                                      size_hint_y=None, height=dp(40), font_size='10sp')
            btn_limpar.bind(on_release=self.confirmar_limpar_historico)
            layout.add_widget(btn_limpar)

        layout.add_widget(Label(text="Nova Senha:", font_size='11sp'))
        self.nova_p = TextInput(password=True, multiline=False, size_hint_y=None, height=dp(35))
        btn_p = BotaoRedondo(text="MUDAR SENHA", cor_fundo=(0.1,0.5,0.3,1), 
                             size_hint_y=None, height=dp(40), font_size='10sp')
        btn_p.bind(on_release=lambda x: PopupConfirmarAcao("Mudar Senha", self.executar_mudanca).open())
        layout.add_widget(self.nova_p)
        layout.add_widget(btn_p)

        btn_atualizar = BotaoRedondo(text="ATUALIZAR CONTATOS", cor_fundo=(0.2,0.5,0.6,1), 
                                     size_hint_y=None, height=dp(40), font_size='10sp')
        btn_atualizar.bind(on_release=self.atualizar_contatos)
        layout.add_widget(btn_atualizar)

        btn_config = BotaoRedondo(text="CONFIGURACOES", cor_fundo=(0.2,0.5,0.6,1), 
                                  size_hint_y=None, height=dp(40), font_size='10sp')
        btn_config.bind(on_release=lambda x: ConfiguracoesPopup().open())
        layout.add_widget(btn_config)

        btn_logout = BotaoRedondo(text="LOGOUT", cor_fundo=(0.2,0.4,0.6,1), 
                                  size_hint_y=None, height=dp(45), font_size='11sp')
        btn_logout.bind(on_release=self.fazer_logout)
        layout.add_widget(btn_logout)

        btn_del = BotaoRedondo(text="EXCLUIR CONTA", cor_fundo=(0.7,0.2,0.2,1), 
                               size_hint_y=None, height=dp(45), font_size='11sp')
        btn_del.bind(on_release=lambda x: PopupConfirmarAcao("Excluir Conta", self.executar_exclusao).open())
        layout.add_widget(btn_del)

        btn_fechar_app = BotaoRedondo(text="SAIR DO PROGRAMA", cor_fundo=(0.4,0,0,1), 
                                      size_hint_y=None, height=dp(45), font_size='11sp')
        btn_fechar_app.bind(on_release=lambda x: App.get_running_app().stop())
        layout.add_widget(btn_fechar_app)

        self.content = layout

    def confirmar_limpar_historico(self, instance):
        """Exibe um pop-up de confirmação antes de limpar o histórico."""
        if not destinatario_atual:
            return
        conteudo = BoxLayout(orientation='vertical', padding=dp(15), spacing=dp(10))
        conteudo.add_widget(Label(text=f"Tem certeza que deseja limpar\ntodo o histórico com [b]{destinatario_atual}[/b]?",
                                  markup=True, halign='center'))
        btn_box = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(45))
        btn_sim = BotaoRedondo(text="SIM, LIMPAR", cor_fundo=(0.7, 0.2, 0.2, 1))
        btn_nao = BotaoRedondo(text="CANCELAR", cor_fundo=(0.3, 0.3, 0.3, 1))
        popup_confirm = Popup(title="Confirmar Limpeza", content=conteudo, size_hint=(0.7, 0.3))
        btn_sim.bind(on_release=lambda x: self._executar_limpeza(popup_confirm))
        btn_nao.bind(on_release=popup_confirm.dismiss)
        btn_box.add_widget(btn_nao)
        btn_box.add_widget(btn_sim)
        conteudo.add_widget(btn_box)
        popup_confirm.content = conteudo
        popup_confirm.open()

    def _executar_limpeza(self, popup_confirm):
        popup_confirm.dismiss()
        self.limpar_historico_atual(None)

    def limpar_historico_atual(self, instance):
        if destinatario_atual:
            try:
                conn = sqlite3.connect(get_path())
                c = conn.cursor()
                c.execute('DELETE FROM mensagens WHERE (usuario=? AND destinatario=?) OR (usuario=? AND destinatario=?)',
                         (USUARIO_LOGADO, destinatario_atual, destinatario_atual, USUARIO_LOGADO))
                c.execute('DELETE FROM ultimas_conversas WHERE usuario=? AND contato=?',
                         (USUARIO_LOGADO, destinatario_atual))
                conn.commit()
                conn.close()
                Clock.schedule_once(lambda dt: Toast(f"Historico com {destinatario_atual} limpo!", cor=(0,0.5,0,0.9)).open())
                # Agora chamamos o método correto em ChatTelaContent
                chat_screen = self.chat_ui
                if hasattr(chat_screen, 'layout_principal') and hasattr(chat_screen.layout_principal, 'limpar_chat'):
                    chat_screen.layout_principal.limpar_chat()
            except Exception as err:
                Clock.schedule_once(lambda dt, e=err: Toast(f"Erro: {e}", cor=(0.7,0,0,0.9)).open())

    def executar_mudanca(self):
        nova = self.nova_p.text.strip()
        if nova:
            Toast("Funcionalidade em desenvolvimento.", cor=(0.7,0.5,0,0.9)).open()
            self.dismiss()

    def executar_exclusao(self):
        Toast("Funcionalidade em desenvolvimento.", cor=(0.7,0.5,0,0.9)).open()
        self.fazer_logout(None)

    def atualizar_contatos(self, instance):
        if sio.connected:
            sio.emit('solicitar_contatos')
            Toast("Solicitando atualizacao...", cor=(0,0.5,0,0.9)).open()
        else:
            Toast("Nao conectado ao servidor.", cor=(0.7,0,0,0.9)).open()

    def fazer_logout(self, instance):
        self.dismiss()
        global USUARIO_LOGADO, chaves_amigos, priv_key, pub_key, destinatario_atual, usuarios_online, todos_contatos
        if sio.connected: 
            sio.disconnect()
        USUARIO_LOGADO = None
        chaves_amigos = {}
        priv_key = None
        pub_key = None
        destinatario_atual = None
        usuarios_online = []
        todos_contatos = []
        Clock.schedule_once(lambda dt: setattr(App.get_running_app().root, 'current', 'login'))

class RegistroTela(Screen):
    def __init__(self, **kwargs):
        super().__init__(name='registro', **kwargs)
        layout = BoxLayout(orientation='vertical', padding=dp(40), spacing=dp(15))
        with layout.canvas.before: 
            Color(0.1, 0.1, 0.1, 1)
            self.rect = Rectangle()
        layout.bind(size=self._update_rect, pos=self._update_rect)
        layout.add_widget(Label(text="CRIAR CONTA", font_size='24sp', bold=True))
        self.new_u = TextInput(hint_text="Usuario", multiline=False, height=dp(55), size_hint_y=None)
        self.new_p = TextInput(hint_text="Senha", password=True, multiline=False, height=dp(55), size_hint_y=None)
        self.confirm_p = TextInput(hint_text="Confirmar senha", password=True, multiline=False, height=dp(55), size_hint_y=None)
        btn_reg = BotaoRedondo(text="REGISTRAR", cor_fundo=(0, 0.5, 0.4, 1), height=dp(60), size_hint_y=None)
        btn_reg.bind(on_release=self.registrar)
        btn_voltar = BotaoRedondo(text="VOLTAR", cor_fundo=(0.2, 0.4, 0.6, 1), height=dp(50), size_hint_y=None)
        btn_voltar.bind(on_release=lambda x: setattr(self.manager, 'current', 'login'))
        btn_fechar = BotaoRedondo(text="FECHAR", cor_fundo=(0.7, 0.2, 0.2, 1), height=dp(50), size_hint_y=None)
        btn_fechar.bind(on_release=lambda x: App.get_running_app().stop())
        layout.add_widget(self.new_u)
        layout.add_widget(self.new_p)
        layout.add_widget(self.confirm_p)
        layout.add_widget(btn_reg)
        layout.add_widget(btn_voltar)
        layout.add_widget(btn_fechar)
        self.add_widget(layout)

    def _update_rect(self, i, v): 
        self.rect.pos = i.pos
        self.rect.size = i.size

    def registrar(self, inst):
        u = self.new_u.text.strip()
        p = self.new_p.text.strip()
        confirm = self.confirm_p.text.strip()
        if not u or not p or not confirm:
            Clock.schedule_once(lambda dt: Toast("Preencha todos os campos!").open())
            return
        if p != confirm:
            Clock.schedule_once(lambda dt: Toast("As senhas não coincidem!", cor=(0.7,0,0,0.9)).open())
            return
        senha_hash = gerar_hash(p)
        sio.emit('registrar_usuario_credencial', {'username': u, 'password_hash': senha_hash})
        Clock.schedule_once(lambda dt: Toast("Solicitando registro...", cor=(0,0.5,0,0.9)).open())

class LoginTela(Screen):
    def __init__(self, **kwargs):
        super().__init__(name='login', **kwargs)

        layout = BoxLayout(orientation='vertical', padding=dp(40), spacing=dp(15))

        top_spacer = BoxLayout(size_hint_y=None, height=dp(40))
        layout.add_widget(top_spacer)

        logo_filename = "Logo.png"
        logo_path = None
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(script_dir, logo_filename)
        if os.path.exists(candidate):
            logo_path = candidate
        if not logo_path and os.path.exists(logo_filename):
            logo_path = logo_filename
        if not logo_path and getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            candidate = os.path.join(exe_dir, logo_filename)
            if os.path.exists(candidate):
                logo_path = candidate

        if logo_path:
            try:
                logo_wrapper = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(160))
                logo_wrapper.add_widget(Widget(size_hint_y=None, height=dp(25)))
                logo_center = AnchorLayout(anchor_x='center', size_hint_y=None, height=dp(120))
                logo = Image(source=logo_path, size_hint=(None, None), size=(dp(420), dp(420)))
                logo_center.add_widget(logo)
                logo_wrapper.add_widget(logo_center)
                layout.add_widget(logo_wrapper)
            except Exception as e:
                print(f"[ERRO] Falha ao carregar imagem: {e}")
                layout.add_widget(Label(text="[PSYCHC]", font_size='30sp', bold=True, markup=True,
                                        size_hint_y=None, height=dp(80), halign='center'))
        else:
            layout.add_widget(Label(text="[HERMES]", font_size='30sp', bold=True, markup=True,
                                    size_hint_y=None, height=dp(80), halign='center'))

        layout.add_widget(Label(text="Bem vindo ao HERMES", font_size='20sp', bold=True,
                                size_hint_y=None, height=dp(40), halign='center'))

        self.user_in = TextInput(hint_text="Usuario", multiline=False, height=dp(55), size_hint_y=None)
        self.pass_in = TextInput(hint_text="Senha", password=True, multiline=False, height=dp(55), size_hint_y=None)
        self.error_label = Label(text="", color=(1,0,0,1), size_hint_y=None, height=dp(30))

        btn = BotaoRedondo(text="ENTRAR", cor_fundo=(0, 0.5, 0.4, 1), height=dp(60), size_hint_y=None)
        btn.bind(on_release=self.fazer_login)

        btn_reg = BotaoRedondo(text="CRIAR CONTA", cor_fundo=(0.2, 0.4, 0.6, 1), height=dp(50), size_hint_y=None)
        btn_reg.bind(on_release=lambda x: setattr(self.manager, 'current', 'registro'))

        btn_sair = BotaoRedondo(text="SAIR", cor_fundo=(0.7, 0.2, 0.2, 1), height=dp(50), size_hint_y=None)
        btn_sair.bind(on_release=lambda x: App.get_running_app().stop())

        layout.add_widget(self.user_in)
        layout.add_widget(self.pass_in)
        layout.add_widget(self.error_label)
        layout.add_widget(btn)
        layout.add_widget(btn_reg)
        layout.add_widget(btn_sair)

        self.add_widget(layout)

        self.status_label = Label(text="Verificando servidor...",
                                  size_hint=(None, None), size=(dp(220), dp(30)),
                                  pos_hint={'right': 1, 'top': 1},
                                  color=(0.7, 0.7, 0.7, 1), font_size='12sp',
                                  halign='right', valign='middle')
        self.status_label.bind(size=self.status_label.setter('text_size'))
        self.add_widget(self.status_label)

        self.popup_offline_mostrado = False
        self.timer_fechar = None

        Clock.schedule_interval(self.verificar_status_servidor, 15)
        Clock.schedule_once(lambda dt: self.verificar_status_servidor(0), 0.5)

    def verificar_status_servidor(self, dt):
        try:
            response = requests.get(URL_RENDER, timeout=5)
            if response.status_code == 200:
                self.status_label.text = "Servidor online [OK]"
                self.status_label.color = (0, 0.8, 0, 1)
                self.popup_offline_mostrado = False
                if hasattr(self, 'popup_offline') and self.popup_offline:
                    self.popup_offline.dismiss()
                if self.timer_fechar:
                    Clock.unschedule(self.timer_fechar)
                    self.timer_fechar = None
            else:
                self.status_label.text = "Servidor com problemas [?]"
                self.status_label.color = (1, 0.5, 0, 1)
        except requests.exceptions.ConnectionError:
            self.status_label.text = "Servidor dormindo [DORMINDO]\n(ate 1 minuto para ativar)"
            self.status_label.color = (1, 1, 0, 1)
            self.exibir_popup_servidor_offline()
        except Exception:
            self.status_label.text = "Erro ao verificar servidor [ERRO]"
            self.status_label.color = (1, 0, 0, 1)

    def atualizar_status_socket(self, conectado):
        if conectado:
            self.status_label.text = "Servidor online [OK]"
            self.status_label.color = (0, 0.8, 0, 1)
            self.popup_offline_mostrado = False
            if hasattr(self, 'popup_offline') and self.popup_offline:
                self.popup_offline.dismiss()
            if self.timer_fechar:
                Clock.unschedule(self.timer_fechar)
                self.timer_fechar = None
        else:
            self.status_label.text = "Servidor offline [OFF]"
            self.status_label.color = (1, 0, 0, 1)
            self.exibir_popup_servidor_offline()

    def exibir_popup_servidor_offline(self):
        if self.popup_offline_mostrado:
            return
        self.popup_offline_mostrado = True

        conteudo = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(15))
        mensagem = ("O servidor está offline!\n"
                    "Por favor, espere 10 segundos e tente abrir o app novamente.\n"
                    "Pedimos desculpas pelo inconveniente.\n\n"
                    "O aplicativo será fechado em 5 segundos.")
        conteudo.add_widget(Label(text=mensagem, halign='center', valign='middle'))

        self.popup_offline = Popup(title="Servidor Offline",
                                   content=conteudo,
                                   size_hint=(0.85, 0.35),
                                   auto_dismiss=False)
        self.popup_offline.open()

        if self.timer_fechar:
            Clock.unschedule(self.timer_fechar)
        self.timer_fechar = Clock.schedule_once(self.fechar_app, 5)

    def fechar_app(self, dt=None):
        App.get_running_app().stop()

    def fazer_login(self, instance):
        u = self.user_in.text.strip()
        p = self.pass_in.text.strip()
        if not u or not p:
            self.error_label.text = "Preencha todos os campos!"
            return
        if not sio.connected:
            self.error_label.text = "Aguardando conexao com o servidor..."
            Clock.schedule_once(lambda dt: self.fazer_login(instance), 2)
            return
        senha_hash = gerar_hash(p)
        sio.emit('login_usuario', {'username': u, 'password_hash': senha_hash})
        self.error_label.text = "Autenticando..."

class ChatTela(Screen):
    def __init__(self, **kwargs):
        super().__init__(name='chat', **kwargs)
        self.layout_principal = ChatTelaContent(chat_screen=self)
        self.add_widget(self.layout_principal)

    def atualizar_status(self, online):
        if hasattr(self.layout_principal, 'st_text'):
            if online:
                self.layout_principal.led_color.rgba = (0, 1, 0, 1)
                self.layout_principal.st_text.text = "Online"
            else:
                self.layout_principal.led_color.rgba = (1, 0, 0, 1)
                self.layout_principal.st_text.text = "Offline"

    def carregar_historico_conversa(self, contato):
        mensagens = carregar_historico(USUARIO_LOGADO, contato, 100)
        self.layout_principal.chat_container.clear_widgets()
        for msg, tipo, timestamp, entregue in mensagens:
            if tipo == 'enviada':
                status = " [ENTREGUE]" if entregue else " [PENDENTE]"
                self.layout_principal.adicionar_mensagem(f"[Voce]{status}: {msg}", 'right', (0.05, 0.4, 0.2, 1))
            else:
                self.layout_principal.adicionar_mensagem(f"[{contato}]: {msg}", 'left', (0.2, 0.2, 0.25, 1))
        marcar_mensagens_lidas(USUARIO_LOGADO, contato)
        app = App.get_running_app()
        if app and hasattr(app, 'atualizar_indicador_nao_lidas'):
            app.atualizar_indicador_nao_lidas()

class ChatTelaContent(BoxLayout):
    def __init__(self, chat_screen, **kwargs):
        super().__init__(orientation='vertical', padding=dp(10), spacing=dp(10), **kwargs)
        self.chat_screen = chat_screen

        self.top_bar = BoxLayout(size_hint_y=None, height=dp(65), spacing=dp(10))
        self.btn_menu = BotaoRedondo(text="MENU", cor_fundo=(0.3, 0.3, 0.3, 1), 
                                      size_hint_x=None, width=dp(95), bold=True)
        self.btn_menu.bind(on_release=self.abrir_menu)
        self.top_bar.add_widget(self.btn_menu)

        self.status_box = BoxLayout(size_hint_x=0.4, spacing=dp(8))
        self.led_canvas = Label(size_hint=(None, None), size=(dp(15), dp(40)))
        with self.led_canvas.canvas:
            self.led_color = Color(1, 0.2, 0.2, 1)
            self.led_circle = Ellipse(size=(dp(13), dp(13)))
        self.led_canvas.bind(pos=self._update_led)
        self.st_text = Label(text="Offline", font_size='14sp', bold=True, size_hint_x=None)
        self.status_box.add_widget(self.led_canvas)
        self.status_box.add_widget(self.st_text)
        self.top_bar.add_widget(self.status_box)

        self.destinatario_label = Label(text="Nenhum destinatario", size_hint_x=0.4, 
                                         color=(0.7, 0.7, 0.7, 1), font_size='12sp', shorten=True)
        self.top_bar.add_widget(self.destinatario_label)

        self.btn_selecionar = BotaoRedondo(text="SELECIONAR", cor_fundo=(0.2, 0.4, 0.6, 1), 
                                            size_hint_x=None, width=dp(100), font_size='11sp')
        self.btn_selecionar.bind(on_release=self.mostrar_usuarios)
        self.top_bar.add_widget(self.btn_selecionar)

        self.add_widget(self.top_bar)

        self.scroll = ScrollView(do_scroll_x=False)
        self.chat_container = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(15), 
                                         padding=(dp(10), dp(10)))
        self.chat_container.bind(minimum_height=self.chat_container.setter('height'))
        self.scroll.add_widget(self.chat_container)
        self.add_widget(self.scroll)

        self.input_area = BoxLayout(size_hint_y=None, height=dp(65), spacing=dp(10))
        self.msg_in = TextInput(multiline=False, background_color=(0.12, 0.12, 0.12, 1), 
                                foreground_color=(1,1,1,1), font_size='16sp', 
                                padding=[dp(12), dp(15)])
        self.msg_in.bind(on_text=self.on_texto_digitado)
        self.msg_in.bind(on_text_validate=self.fire_send)
        self.btn_env = BotaoRedondo(text="ENVIAR", cor_fundo=(0, 0.5, 0.4, 1), 
                                     size_hint_x=None, width=dp(90), bold=True, font_size='12sp')
        self.btn_env.bind(on_release=self.fire_send)
        self.input_area.add_widget(self.msg_in)
        self.input_area.add_widget(self.btn_env)
        self.add_widget(self.input_area)

        self.indicador_digitacao = Label(text="", size_hint_y=None, height=dp(20), color=(0.5,0.5,0.5,1))
        self.add_widget(self.indicador_digitacao)

    def on_texto_digitado(self, instance, value):
        if value.strip():
            emitir_digitando()

    def mostrar_usuarios(self, instance):
        global todos_contatos
        if not todos_contatos:
            Clock.schedule_once(lambda dt: Toast("Nenhum contato encontrado. Atualizando...", cor=(0.7,0.5,0,0.9)).open())
            sio.emit('solicitar_contatos')
            return
        popup = Popup(title="Selecionar Destinatario", size_hint=(0.9, 0.7))
        layout = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        scroll = ScrollView()
        lista = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(5))
        lista.bind(minimum_height=lista.setter('height'))
        online = [c for c in todos_contatos if c['online']]
        offline = [c for c in todos_contatos if not c['online']]
        if online:
            lista.add_widget(Label(text="[ON] ONLINE", size_hint_y=None, height=dp(30), bold=True))
            for c in online:
                btn = BotaoRedondo(text=f"[ON] {c['username']}", cor_fundo=(0.2,0.5,0.2,1),
                                  size_hint_y=None, height=dp(45), font_size='12sp')
                btn.bind(on_release=lambda x, u=c['username']: self.selecionar_destinatario(u, popup))
                lista.add_widget(btn)
        if offline:
            if online:
                lista.add_widget(Label(text="--- OFFLINE ---", size_hint_y=None, height=dp(30)))
            for c in offline:
                btn = BotaoRedondo(text=f"[OFF] {c['username']}", cor_fundo=(0.3,0.3,0.5,1),
                                  size_hint_y=None, height=dp(45), font_size='12sp')
                btn.bind(on_release=lambda x, u=c['username']: self.selecionar_destinatario(u, popup))
                lista.add_widget(btn)
        scroll.add_widget(lista)
        layout.add_widget(scroll)
        btn_atualizar = BotaoRedondo(text="ATUALIZAR LISTA", cor_fundo=(0.2,0.5,0.6,1), size_hint_y=None, height=dp(40))
        btn_atualizar.bind(on_release=lambda x: self.atualizar_lista_manual(popup))
        layout.add_widget(btn_atualizar)
        btn_fechar = BotaoRedondo(text="FECHAR", cor_fundo=(0.5,0.2,0.2,1), size_hint_y=None, height=dp(40))
        btn_fechar.bind(on_release=popup.dismiss)
        layout.add_widget(btn_fechar)
        popup.content = layout
        popup.open()

    def atualizar_lista_manual(self, popup):
        if sio.connected:
            Clock.schedule_once(lambda dt: Toast("Atualizando lista...", cor=(0,0.5,0,0.9)).open())
            sio.emit('solicitar_contatos')
            popup.dismiss()
            Clock.schedule_once(lambda dt: self.mostrar_usuarios(None), 1)

    def selecionar_destinatario(self, usuario, popup):
        global destinatario_atual
        destinatario_atual = usuario
        self.destinatario_label.text = f"Para: {usuario}"
        self.chat_screen.carregar_historico_conversa(usuario)
        sio.emit('solicitar_historico', {'contato': usuario})
        popup.dismiss()

    def adicionar_mensagem(self, texto, alinhamento, cor):
        anchor = AnchorLayout(anchor_x=alinhamento, size_hint_y=None)
        balao = BalaoMensagem(text=texto, cor_fundo=cor)
        anchor.height = balao.height
        anchor.add_widget(balao)
        self.chat_container.add_widget(anchor)
        Clock.schedule_once(lambda d: setattr(self.scroll, 'scroll_y', 0), 0.1)

    def limpar_chat(self):
        self.chat_container.clear_widgets()

    def abrir_menu(self, instance):
        MenuOpcoes(chat_instance=self.chat_screen).open()

    def _update_led(self, *args):
        if hasattr(self, 'led_canvas') and hasattr(self, 'led_circle'):
            self.led_circle.pos = (self.led_canvas.x, self.led_canvas.y + dp(26))

    def fire_send(self, inst):
        if self.msg_in.text.strip() and destinatario_atual:
            App.get_running_app().enviar_mensagem(self.msg_in.text, destinatario_atual)
            self.msg_in.text = ""
            self.indicador_digitacao.text = ""
        elif not destinatario_atual:
            Clock.schedule_once(lambda dt: Toast("Selecione um destinatario primeiro!", cor=(0.7,0.5,0,0.9)).open())

    def mostrar_indicador_digitacao(self, usuario):
        self.indicador_digitacao.text = f"{usuario} esta digitando..."
        Clock.schedule_once(lambda dt: self.cancelar_indicador_digitacao(), 2)

    def cancelar_indicador_digitacao(self):
        self.indicador_digitacao.text = ""

class ChatApp(App):
    def build(self):
        init_chat_db()
        self.sm = ScreenManager(transition=NoTransition())
        self.sm.add_widget(LoginTela())
        self.sm.add_widget(RegistroTela())
        self.sm.add_widget(ChatTela())
        return self.sm

    def on_start(self):
        threading.Thread(target=conectar_servidor, daemon=True).start()

    def mostrar_indicador_digitacao(self, usuario):
        chat = self.sm.get_screen('chat').layout_principal
        if chat.destinatario_atual == usuario:
            chat.mostrar_indicador_digitacao(usuario)

    def cancelar_indicador_digitacao(self):
        chat = self.sm.get_screen('chat').layout_principal
        chat.cancelar_indicador_digitacao()

    def atualizar_indicador_nao_lidas(self):
        try:
            nao_lidas = contar_mensagens_nao_lidas(USUARIO_LOGADO)
            nao_entregues = contar_mensagens_nao_entregues(USUARIO_LOGADO)
            chat = self.sm.get_screen('chat').layout_principal
            qtd_online = len(usuarios_online)
            total_nao_lidas = sum(nao_lidas.values())
            total_pendentes = sum(nao_entregues.values())
            if total_nao_lidas > 0:
                chat.btn_selecionar.text = f"SELECIONAR ({total_nao_lidas} !)"
                chat.btn_selecionar.cor_fundo = (0.7, 0.3, 0, 1)
            elif total_pendentes > 0:
                chat.btn_selecionar.text = f"SELECIONAR ({total_pendentes} pend)"
                chat.btn_selecionar.cor_fundo = (0.5, 0.5, 0, 1)
            else:
                chat.btn_selecionar.text = f"SELECIONAR ({qtd_online} on)"
                chat.btn_selecionar.cor_fundo = (0.2, 0.4, 0.6, 1)
        except Exception as e:
            print(f"[ERRO] Ao atualizar indicador: {e}")

    def log_na_tela(self, txt, cor, alin):
        def _add(dt):
            try:
                if self.sm.current == 'chat':
                    chat = self.sm.get_screen('chat').layout_principal
                    chat.adicionar_mensagem(txt, alin, cor)
            except Exception as e:
                print(f"[ERRO] Ao adicionar mensagem: {e}")
        Clock.schedule_once(_add)

    def enviar_mensagem(self, txt, destinatario):
        salvar_mensagem(USUARIO_LOGADO, destinatario, txt, 'enviada')
        self.log_na_tela(f"[Voce]: {txt}", (0.05, 0.4, 0.2, 1), 'right')
        self.log_na_tela(f"[ENVIANDO] Para {destinatario}...", (0.5, 0.5, 0.5, 0.6), 'center')
        if destinatario in chaves_amigos:
            try:
                chave_destinatario = chaves_amigos[destinatario]
                msg_cifrada = rsa.encrypt(txt.encode(), chave_destinatario)
                msg_base64 = base64.b64encode(msg_cifrada).decode('utf-8')
                pacote = {'to': destinatario, 'from': USUARIO_LOGADO, 'content': msg_base64}
                sio.emit('message', pacote)
                print(f"[OK] Mensagem ENCRIPTADA enviada para {destinatario}")
            except Exception as e:
                self.log_na_tela(f"[ERRO] {e}", (0.5, 0, 0, 1), 'center')
                print(f"[ERRO] {e}")
        else:
            try:
                pacote = {'to': destinatario, 'from': USUARIO_LOGADO, 'content': txt}
                sio.emit('message', pacote)
                print(f"[INFO] Mensagem enviada para {destinatario} (sera entregue quando online)")
            except Exception as e:
                print(f"[ERRO] Ao enviar: {e}")

if __name__ == "__main__":
    ChatApp().run()
