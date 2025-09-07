#!/usr/bin/env python3
"""
Bot Discord com Acesso Direto ao Banco - Trading Class
Versão otimizada para Railway com suas credenciais
"""

import discord
from discord import app_commands, ui
from discord.ext import tasks
import os
import mysql.connector
from datetime import datetime, timedelta
import hashlib
import secrets

# Configuração do banco (usando variáveis de ambiente do Railway)
DB_CONFIG = {
    'host': os.environ.get('DB_HOST'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'database': os.environ.get('DB_NAME'),
    'port': int(os.environ.get('DB_PORT', 3306))
}

# Configuração do bot (usando variáveis de ambiente do Railway)
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
GUILD_ID = int(os.environ.get('GUILD_ID'))
ROLE_ALUNO_ID = None
ROLE_MENTORADO_ID = None

REGISTRATION_LINK = "https://aluno.operebem.com.br"
EMBED_COLOR = 0x5865F2

# Validar variáveis de ambiente obrigatórias
def validate_environment():
    """Validar se todas as variáveis de ambiente necessárias estão configuradas"""
    required_vars = [
        'DISCORD_TOKEN',
        'GUILD_ID', 
        'DB_HOST',
        'DB_USER',
        'DB_PASSWORD',
        'DB_NAME'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.environ.get(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Erro: Variáveis de ambiente obrigatórias não encontradas: {', '.join(missing_vars)}")
        print("Configure as seguintes variáveis no Railway:")
        for var in missing_vars:
            print(f"  - {var}")
        return False
    
    print("✅ Todas as variáveis de ambiente estão configuradas!")
    return True

def get_db_connection():
    """Conectar ao banco de dados"""
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Exception as e:
        print(f"Erro ao conectar ao banco: {e}")
        return None

def validate_code(code):
    """Validar código de validação"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                dv.id, dv.user_id, dv.validation_code, dv.subscription_tier,
                dv.purchase_date, dv.plan_end_date, dv.amount_paid, dv.payment_method,
                dv.is_validated, dv.discord_user_id,
                u.nome_completo, u.email, u.subscription_status, u.subscription_expires_at
            FROM discord_validation dv
            JOIN users u ON dv.user_id = u.id
            WHERE dv.validation_code = %s AND u.status = 'active'
        """, (code,))
        
        result = cursor.fetchone()
        
        if result:
            # Verificar se a assinatura ainda está ativa
            is_expired = False
            if result['subscription_expires_at']:
                expires_at = datetime.strptime(str(result['subscription_expires_at']), '%Y-%m-%d %H:%M:%S')
                is_expired = datetime.now() > expires_at
            
            return {
                'success': True,
                'data': {
                    'user_id': result['user_id'],
                    'validation_code': result['validation_code'],
                    'subscription_tier': result['subscription_tier'],
                    'purchase_date': result['purchase_date'],
                    'plan_end_date': result['plan_end_date'],
                    'amount_paid': result['amount_paid'],
                    'payment_method': result['payment_method'],
                    'is_validated': bool(result['is_validated']),
                    'discord_user_id': result['discord_user_id'],
                    'nome_completo': result['nome_completo'],
                    'email': result['email'],
                    'subscription_status': result['subscription_status'],
                    'subscription_expires_at': result['subscription_expires_at'],
                    'is_expired': is_expired,
                    'discord_role': result['subscription_tier']
                }
            }
        else:
            return {
                'success': False,
                'error': 'Código de validação inválido ou usuário inativo'
            }
    
    except Exception as e:
        print(f"Erro na validação: {e}")
        return {
            'success': False,
            'error': 'Erro interno do servidor'
        }
    finally:
        conn.close()

def mark_as_validated(code, discord_user_id, bot_user_id=None):
    """Marcar código como validado"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        
        # Buscar dados do usuário
        cursor.execute("""
            SELECT dv.user_id, dv.subscription_tier, u.subscription_expires_at
            FROM discord_validation dv
            JOIN users u ON dv.user_id = u.id
            WHERE dv.validation_code = %s
        """, (code,))
        
        user = cursor.fetchone()
        if not user:
            return False
        
        user_id, subscription_tier, subscription_expires_at = user
        
        # Atualizar validação
        cursor.execute("""
            UPDATE discord_validation 
            SET is_validated = 1, discord_user_id = %s, role_status = 'assigned', 
                role_assigned_at = NOW(), last_role_check = NOW(), updated_at = NOW()
            WHERE validation_code = %s
        """, (discord_user_id, code))
        
        # Atualizar usuário
        cursor.execute("""
            UPDATE users 
            SET discord_sync_status = 'synced', last_discord_sync = NOW()
            WHERE id = %s
        """, (user_id,))
        
        # Log da ação
        cursor.execute("""
            INSERT INTO discord_role_logs 
            (discord_user_id, user_id, validation_code, action, role_name, subscription_tier, subscription_expires_at, bot_user_id, reason)
            VALUES (%s, %s, %s, 'assign', %s, %s, %s, %s, 'Validação inicial do código')
        """, (discord_user_id, user_id, code, subscription_tier, subscription_tier, subscription_expires_at, bot_user_id))
        
        conn.commit()
        return True
    
    except Exception as e:
        print(f"Erro ao marcar como validado: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def get_expired_users():
    """Buscar usuários com assinatura expirada"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                dv.discord_user_id, dv.subscription_tier,
                u.nome_completo, u.email, u.subscription_expires_at
            FROM discord_validation dv
            JOIN users u ON dv.user_id = u.id
            WHERE u.status = 'active' 
            AND u.subscription_status = 'active'
            AND u.subscription_expires_at < NOW()
            AND dv.discord_user_id IS NOT NULL
            AND dv.is_validated = 1
            ORDER BY u.subscription_expires_at ASC
        """)
        
        return cursor.fetchall()
    
    except Exception as e:
        print(f"Erro ao buscar usuários expirados: {e}")
        return []
    finally:
        conn.close()

def mark_role_removed(discord_user_id, bot_user_id=None):
    """Marcar cargo como removido"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        
        # Buscar dados do usuário
        cursor.execute("""
            SELECT dv.user_id, dv.validation_code, dv.subscription_tier, u.subscription_expires_at
            FROM discord_validation dv
            JOIN users u ON dv.user_id = u.id
            WHERE dv.discord_user_id = %s AND dv.is_validated = 1
        """, (discord_user_id,))
        
        user = cursor.fetchone()
        if not user:
            return False
        
        user_id, validation_code, subscription_tier, subscription_expires_at = user
        
        # Atualizar status do cargo
        cursor.execute("""
            UPDATE discord_validation 
            SET role_status = 'expired', role_removed_at = NOW(), last_role_check = NOW(), updated_at = NOW()
            WHERE discord_user_id = %s
        """, (discord_user_id,))
        
        # Atualizar usuário
        cursor.execute("""
            UPDATE users 
            SET discord_sync_status = 'pending', last_discord_sync = NOW()
            WHERE id = %s
        """, (user_id,))
        
        # Log da ação
        cursor.execute("""
            INSERT INTO discord_role_logs 
            (discord_user_id, user_id, validation_code, action, role_name, subscription_tier, subscription_expires_at, bot_user_id, reason)
            VALUES (%s, %s, %s, 'remove', %s, %s, %s, %s, 'Assinatura expirada - cargo removido automaticamente')
        """, (discord_user_id, user_id, validation_code, subscription_tier, subscription_tier, subscription_expires_at, bot_user_id))
        
        conn.commit()
        return True
    
    except Exception as e:
        print(f"Erro ao marcar cargo como removido: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

# Modal para validação
class ValidationModal(ui.Modal, title="Validação de Acesso"):
    token_input = ui.TextInput(label="Seu Token de Validação", placeholder="Cole aqui o token que você pegou no site...", style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        token = self.token_input.value.strip()
        
        try:
            # Validar código
            result = validate_code(token)
            
            if not result or not result.get('success'):
                error_message = result.get('error', 'Token inválido ou já utilizado.') if result else 'Erro interno do servidor'
                await interaction.followup.send(f"❌ {error_message}", ephemeral=True)
                return

            user_data = result.get('data', {})
            
            if user_data.get('is_expired'):
                await interaction.followup.send("❌ Sua assinatura expirou. Por favor, renove para validar seu acesso.", ephemeral=True)
                return

            tier = user_data.get('subscription_tier')
            role_id_to_add = ROLE_ALUNO_ID if tier == 'Aluno' else ROLE_MENTORADO_ID

            if not role_id_to_add:
                await interaction.followup.send("❌ Erro: Cargo não configurado no bot. Contate um administrador.", ephemeral=True)
                return
            
            guild = interaction.guild
            member = interaction.user
            role_to_add = guild.get_role(role_id_to_add)

            if not role_to_add:
                await interaction.followup.send(f"❌ Erro crítico: O cargo para '{tier}' não foi encontrado. Contate um administrador.", ephemeral=True)
                return
            
            # Remover outros cargos de assinatura
            roles_to_remove_ids = [ROLE_ALUNO_ID, ROLE_MENTORADO_ID]
            roles_to_remove = [role for role in member.roles if role.id in roles_to_remove_ids]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Ajuste de plano de assinatura")

            # Adicionar cargo
            await member.add_roles(role_to_add, reason="Validação de assinatura via site")

            # Marcar como validado no banco
            success = mark_as_validated(token, str(member.id), str(client.user.id))
            
            if success:
                await interaction.followup.send(f"✅ Validação concluída! Você recebeu o cargo **{role_to_add.name}**. Bem-vindo(a)!", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ Cargo adicionado, mas houve um erro ao atualizar o banco. Contate um administrador.", ephemeral=True)

        except Exception as e:
            print(f"Erro inesperado na validação: {e}")
            await interaction.followup.send("❌ Ocorreu um erro inesperado. Contate o suporte.", ephemeral=True)

# View com botões
class ValidationView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✅ Validar", style=discord.ButtonStyle.green, custom_id="persistent_validation_button")
    async def validate_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ValidationModal())

    @ui.button(label="📩 Ainda não sou aluno", style=discord.ButtonStyle.blurple, custom_id="persistent_register_button")
    async def register_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(f"Para se tornar um aluno, [clique aqui]({REGISTRATION_LINK}).", ephemeral=True)

# Cliente do bot
class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

intents = discord.Intents.default()
intents.members = True
client = MyClient(intents=intents)

# Tarefas automáticas
@tasks.loop(hours=1)
async def check_expired_subscriptions():
    print("Iniciando verificação de assinaturas expiradas...")
    try:
        expired_users = get_expired_users()
        print(f"Encontrados {len(expired_users)} usuários expirados.")
        
        guild = client.get_guild(GUILD_ID)
        if not guild:
            return
        
        for user in expired_users:
            discord_id = user.get('discord_user_id')
            tier = user.get('subscription_tier')
            if not discord_id or not tier:
                continue

            member = guild.get_member(int(discord_id))
            role_id_to_remove = ROLE_ALUNO_ID if tier == 'Aluno' else ROLE_MENTORADO_ID
            role_to_remove = guild.get_role(role_id_to_remove)
            
            if member and role_to_remove and role_to_remove in member.roles:
                await member.remove_roles(role_to_remove, reason="Assinatura expirada")
                
                # Marcar como removido no banco
                mark_role_removed(discord_id, str(client.user.id))

                print(f"Cargo '{role_to_remove.name}' removido de {member.name}.")
                try:
                    await member.send(f"Olá! Notamos que sua assinatura {tier} expirou. Seu cargo foi removido. Para renovar, visite: {REGISTRATION_LINK}")
                except discord.Forbidden:
                    print(f"Não foi possível enviar DM para {member.name}.")

    except Exception as e:
        print(f"Erro na verificação de expirados: {e}")

@client.event
async def on_ready():
    global ROLE_ALUNO_ID, ROLE_MENTORADO_ID
    
    # Carregar IDs dos cargos (você pode configurar via variáveis de ambiente)
    ROLE_ALUNO_ID = int(os.environ.get('ROLE_ALUNO_ID', 0)) or None
    ROLE_MENTORADO_ID = int(os.environ.get('ROLE_MENTORADO_ID', 0)) or None
    
    client.add_view(ValidationView())
    if not check_expired_subscriptions.is_running():
        check_expired_subscriptions.start()
        
    print(f'✅ Bot {client.user} está online e pronto!')
    print(f'Cargo Aluno ID: {ROLE_ALUNO_ID}')
    print(f'Cargo Mentorado ID: {ROLE_MENTORADO_ID}')

# Comandos administrativos
@client.tree.command(name="status", description="Verifica o status do sistema.")
@app_commands.default_permissions(administrator=True)
async def status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    embed = discord.Embed(title="📊 Status do Sistema de Integração", color=EMBED_COLOR)
    
    try:
        # Testar conexão com banco
        conn = get_db_connection()
        if conn:
            embed.add_field(name="Conexão com Banco", value="✅ Sucesso", inline=False)
            
            # Contar usuários
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'active'")
            total_users = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM discord_validation WHERE is_validated = 1")
            validated_users = cursor.fetchone()[0]
            
            expired_users = get_expired_users()
            
            embed.add_field(name="Usuários Ativos", value=str(total_users), inline=True)
            embed.add_field(name="Usuários Validados", value=str(validated_users), inline=True)
            embed.add_field(name="Usuários Expirados", value=str(len(expired_users)), inline=True)
            
            conn.close()
        else:
            embed.add_field(name="Conexão com Banco", value="❌ Falha", inline=False)
    except Exception as e:
        embed.add_field(name="Conexão com Banco", value=f"❌ Erro: {e}", inline=False)

    embed.add_field(name="ID Cargo Aluno", value=f"`{ROLE_ALUNO_ID}`" if ROLE_ALUNO_ID else "Não configurado", inline=False)
    embed.add_field(name="ID Cargo Mentorado", value=f"`{ROLE_MENTORADO_ID}`" if ROLE_MENTORADO_ID else "Não configurado", inline=False)
    
    await interaction.followup.send(embed=embed)

@client.tree.command(name="configurar_cargos", description="Configura os IDs dos cargos Aluno e Mentorado.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(aluno="O cargo para membros Alunos.", mentorado="O cargo para membros Mentorados.")
async def configure_roles(interaction: discord.Interaction, aluno: discord.Role, mentorado: discord.Role):
    await interaction.response.defer(ephemeral=True)
    
    global ROLE_ALUNO_ID, ROLE_MENTORADO_ID
    ROLE_ALUNO_ID = aluno.id
    ROLE_MENTORADO_ID = mentorado.id

    await interaction.followup.send("✅ IDs dos cargos configurados com sucesso!")

@client.tree.command(name="enviar_painel_validacao", description="Envia o painel de validação fixo neste canal.")
@app_commands.default_permissions(administrator=True)
async def send_validation_panel(interaction: discord.Interaction):
    embed = discord.Embed(title="🔑 Área Exclusiva para Alunos TradingClass", description="Clique no botão abaixo para inserir seu TOKEN único e liberar seu cargo:", color=EMBED_COLOR)
    await interaction.channel.send(embed=embed, view=ValidationView())
    await interaction.response.send_message("Painel de validação enviado!", ephemeral=True)

@client.tree.command(name="enviar_boas_vindas", description="Envia a mensagem de boas-vindas neste canal.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(canal_validacao="O canal para onde o botão de validação deve levar.")
async def send_welcome_message(interaction: discord.Interaction, canal_validacao: discord.TextChannel):
    welcome_text = (
        "💎 **COMUNIDADE TRADINGCLASS**\n\n"
        "Este é um espaço exclusivo da OpereBem para quem decidiu evoluir de verdade no mercado.\n"
        "Aqui dentro você terá acesso a:\n\n"
        ":books: Materiais e apostilas para estudo\n"
        ":movie_camera: Aulas e treinamentos organizados por módulos\n"
        ":bar_chart: Discussões e análises de mercado em tempo real\n"
        ":busts_in_silhouette: Conexão com professores, traders e outros alunos\n\n"
        f":arrow_right: Para liberar seu acesso, vá até {canal_validacao.mention} e siga as instruções.\n\n"
        "Seu próximo passo como Trader começa agora. :rocket:"
    )
    embed = discord.Embed(description=welcome_text, color=EMBED_COLOR)
    view = ui.View()
    view.add_item(ui.Button(label="Ir para Validação", style=discord.ButtonStyle.link, url=canal_validacao.jump_url))
    view.add_item(ui.Button(label="Ainda não sou aluno", style=discord.ButtonStyle.link, url=REGISTRATION_LINK))
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("Mensagem de boas-vindas enviada!", ephemeral=True)

@client.tree.command(name="regras", description="Envia a mensagem com as regras da comunidade neste canal.")
@app_commands.default_permissions(administrator=True)
async def send_rules(interaction: discord.Interaction):
    rules_text = (
        "1️⃣ **Respeito em primeiro lugar**\n"
        "Trate todos com cordialidade. Não será tolerado preconceito, ataques pessoais, xingamentos ou qualquer forma de discriminação.\n\n"
        "2️⃣ **Sem spam**\n"
        "Evite flood de mensagens, áudios ou imagens desnecessárias. Links externos só com autorização da moderação.\n\n"
        "3️⃣ **Foco no aprendizado**\n"
        "Essa comunidade é sobre trading, mercado financeiro e desenvolvimento. Mantenha os tópicos relevantes dentro de cada canal.\n\n"
        "4️⃣ **Nada de calls ou sinais de trade**\n"
        "O objetivo aqui é educacional. Não compartilhe calls de compra/venda ou promessas de ganhos fáceis.\n\n"
        "5️⃣ **Ambiente saudável**\n"
        "Não poste conteúdos ofensivos, violentos, políticos ou de cunho sexual.\n\n"
        "6️⃣ **Ajuda mútua e colaboração**\n"
        "Compartilhe conhecimento, tire dúvidas, incentive a evolução dos colegas. A comunidade cresce junto.\n\n"
        "7️⃣ **Divulgação de terceiros**\n"
        "Proibido divulgar cursos, canais ou serviços externos sem autorização da equipe.\n\n"
        "8️⃣ **Confidencialidade**\n"
        "Respeite o conteúdo exclusivo da TradingClass. Não compartilhe materiais pagos fora do servidor.\n\n"
        "9️⃣ **Respeite a moderação**\n"
        "A equipe de moderadores está aqui para organizar. Questione com respeito e siga as orientações.\n\n"
        "🔟 **Tenha paciência**\n"
        "Nem sempre sua dúvida será respondida na hora. Espere com calma e continue participando.\n\n"
        "✅ Ao utilizar a comunidade, você declara que leu e concorda com os Termos de Uso da TradingClass."
    )
    embed = discord.Embed(title="📜 Regras da Comunidade TradingClass", description=rules_text, color=EMBED_COLOR)
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("Mensagem de regras enviada!", ephemeral=True)

# Executar o bot
if __name__ == "__main__":
    print("🚀 Iniciando Bot Discord Trading Class...")
    print("=" * 50)
    
    # Validar variáveis de ambiente
    if not validate_environment():
        print("❌ Bot não pode ser iniciado. Configure as variáveis de ambiente no Railway.")
        exit(1)
    
    # Mostrar configuração (sem senhas)
    print(f"Discord Token: {'✅ Configurado' if DISCORD_TOKEN else '❌ Não configurado'}")
    print(f"Guild ID: {GUILD_ID}")
    print(f"Banco: {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print(f"Database: {DB_CONFIG['database']}")
    print(f"User: {DB_CONFIG['user']}")
    print("=" * 50)
    
    try:
        client.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"❌ Erro ao iniciar bot: {e}")
        exit(1)
