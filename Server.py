import eventlet
eventlet.monkey_patch()  

from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from datetime import datetime
import json
import os

app = Flask(__name__)
# SocketIO configurado para produção
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Armazenar usuários online
usuarios = {}  # username -> {'sid': sid, 'public_key': key}

# Armazenar mensagens offline
mensagens_offline = {}  # username -> [{'from': user, 'content': msg, 'timestamp': time}]

# Rota de status HTTP
@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'usuarios_online': len(usuarios),
        'mensagens_pendentes': {u: len(msgs) for u, msgs in mensagens_offline.items()},
        'lista_usuarios': list(usuarios.keys())
    })

@app.route('/status')
def status():
    return jsonify({
        'status': 'online',
        'usuarios_online': len(usuarios),
        'mensagens_offline': {u: len(msgs) for u, msgs in mensagens_offline.items()},
        'lista_usuarios': list(usuarios.keys())
    })

# Evento de conexão
@socketio.on('connect')
def handle_connect():
    print(f'[CONNECT] Cliente conectado: {request.sid}')

# Evento de desconexão
@socketio.on('disconnect')
def handle_disconnect():
    # Encontrar e remover usuário
    username_to_remove = None
    for username, data in usuarios.items():
        if data.get('sid') == request.sid:
            username_to_remove = username
            break
    
    if username_to_remove:
        del usuarios[username_to_remove]
        print(f'[DISCONNECT] Usuario desconectado: {username_to_remove}')
        print(f'[INFO] Total de usuarios online: {len(usuarios)}')
        
        # Notificar outros sobre atualização da lista
        lista = [{'username': u, 'public_key': data['public_key']} for u, data in usuarios.items()]
        emit('lista_usuarios', lista, broadcast=True)
        print(f'[INFO] Lista de usuarios atualizada para {len(lista)} usuarios')

# Evento de registro de usuário
@socketio.on('registrar_usuario')
def handle_registrar_usuario(data):
    print(f'[REGISTRO] Recebido: {data}')
    
    username = data.get('username')
    public_key = data.get('public_key')
    
    if username and public_key:
        usuarios[username] = {
            'sid': request.sid,
            'public_key': public_key
        }
        
        print(f'[OK] Usuario registrado: {username}')
        print(f'[INFO] Total de usuarios online: {len(usuarios)}')
        
        # Criar lista de usuários atuais
        lista_usuarios = []
        for user, info in usuarios.items():
            lista_usuarios.append({
                'username': user,
                'public_key': info['public_key']
            })
        
        # Envia lista para todos os usuários
        print(f'[INFO] Enviando lista de usuarios para todos: {len(lista_usuarios)} usuarios')
        emit('lista_usuarios', lista_usuarios, broadcast=True)
        
        # Envia chave do novo usuário para todos os outros
        emit('chave_usuario', {
            'username': username,
            'public_key': public_key
        }, broadcast=True, include_self=False)
        
        # VERIFICA SE HÁ MENSAGENS OFFLINE PARA ESTE USUÁRIO
        if username in mensagens_offline and mensagens_offline[username]:
            qtd = len(mensagens_offline[username])
            print(f'[OFFLINE] Entregando {qtd} mensagens pendentes para {username}')
            
            for msg in mensagens_offline[username]:
                # Entrega cada mensagem offline
                emit('message', {
                    'from': msg['from'],
                    'content': msg['content'],
                    'offline': True,
                    'timestamp': msg['timestamp']
                }, room=request.sid)
                print(f'[ENTREGA] Mensagem de {msg["from"]} entregue para {username}')
            
            # Limpa mensagens entregues
            del mensagens_offline[username]
            print(f'[OFFLINE] Todas as mensagens de {username} foram entregues')
        else:
            print(f'[INFO] Nenhuma mensagem offline para {username}')

# Evento para solicitar lista de usuários
@socketio.on('solicitar_usuarios')
def handle_solicitar_usuarios():
    print(f'[SOLICITAR] Lista de usuarios solicitada')
    
    lista_usuarios = []
    for user, info in usuarios.items():
        lista_usuarios.append({
            'username': user,
            'public_key': info['public_key']
        })
    
    print(f'[INFO] Enviando lista de {len(lista_usuarios)} usuarios')
    emit('lista_usuarios', lista_usuarios)

# Evento original para compatibilidade com versões anteriores
@socketio.on('enviar_chave')
def handle_key(data):
    print(f'[KEY] Enviando chave: {data}')
    # Repassa sua chave pública para os outros usuários
    emit('receber_chave', data, broadcast=True, include_self=False)

# Evento de mensagem atualizado com suporte offline
@socketio.on('message')
def handle_message(data):
    print(f'[MENSAGEM] Recebida: {data}')
    
    # Verifica se é o novo formato (com 'to' e 'from')
    if isinstance(data, dict) and 'to' in data and 'from' in data:
        to = data.get('to')
        from_user = data.get('from')
        content = data.get('content')
        
        if to and from_user and content:
            destinatario = usuarios.get(to)
            
            if destinatario:
                # Destinatário está ONLINE - entrega imediata
                print(f'[ENTREGA] De {from_user} para {to} (ONLINE)')
                emit('message', {
                    'from': from_user,
                    'content': content,
                    'offline': False,
                    'timestamp': datetime.now().isoformat()
                }, room=destinatario['sid'])
                
                # Confirmação de entrega para o remetente
                emit('delivery_confirmation', {
                    'to': to,
                    'from': from_user,
                    'status': 'delivered',
                    'timestamp': datetime.now().isoformat()
                }, room=request.sid)
                
                print(f'[OK] Mensagem entregue para {to}')
                
            else:
                # Destinatário está OFFLINE - armazena mensagem
                print(f'[OFFLINE] Armazenando mensagem de {from_user} para {to}')
                
                if to not in mensagens_offline:
                    mensagens_offline[to] = []
                
                mensagens_offline[to].append({
                    'from': from_user,
                    'content': content,
                    'timestamp': datetime.now().isoformat()
                })
                
                qtd_pendentes = len(mensagens_offline[to])
                print(f'[ARMAZENADA] {to} tem {qtd_pendentes} mensagem(ns) pendente(s)')
                
                # Notifica remetente que a mensagem foi armazenada
                emit('delivery_confirmation', {
                    'to': to,
                    'from': from_user,
                    'status': 'stored_offline',
                    'timestamp': datetime.now().isoformat()
                }, room=request.sid)
                
                print(f'[CONFIRMACAO] Remetente {from_user} notificado sobre armazenamento')
        else:
            print(f'[ERRO] Dados incompletos na mensagem: {data}')
    else:
        # Formato antigo - apenas repassa (broadcast)
        print(f'[BROADCAST] Repassando mensagem no formato antigo')
        emit('message', data, broadcast=True, include_self=False)

# Evento para capturar eventos não tratados (debug)
@socketio.on_error_default
def default_error_handler(e):
    print(f'[ERRO] Evento nao tratado: {e}')

if __name__ == '__main__':
    # Pega a porta do ambiente (Render define essa variável)
    port = int(os.environ.get('PORT', 5000))
    
    print()
    print('=' * 60)
    print('SERVIDOR INICIADO COM SUPORTE A MENSAGENS OFFLINE')
    print('=' * 60)
    print(f'[INFO] Servidor rodando na porta {port}')
    print('[INFO] Status disponivel em: /status')
    print('[INFO] Mensagens offline serao armazenadas e entregues automaticamente')
    print('=' * 60)
    print()
    
    # Para produção, desabilite o debug
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
