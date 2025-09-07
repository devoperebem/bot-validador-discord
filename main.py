import discord
from discord import app_commands, ui
from discord.ext import tasks
import os
import requests

# --- CONFIGURAÇÃO ---
ROLE_MAP = {
    'Aluno': 'Aluno',
    'Mentorado': 'Mentorado'
}
REGISTRATION_LINK = "https://aluno.operebem.com.br"
API_BASE_URL = os.environ.get('API_BASE_URL') 
API_KEY = os.environ.get('API_KEY')
EMBED_COLOR = 0x5865F2 

# --- MODAL: O FORMULÁRIO POP-UP PARA O CÓDIGO ---
class ValidationModal(ui.Modal, title="Validação de Acesso"):
    token_input = ui.TextInput(label="Seu Token de Validação", placeholder="Cole aqui o token que você pegou no site...", style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        token = self.token_input.value.strip()
        headers = {'X-API-Key': API_KEY} # Adicionando o header com a API Key

        try:
            # 1. Chamar a API para validar o código (com autenticação)
            params = {'action': 'validate', 'code': token}
            response = requests.get(API_BASE_URL, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

            if not data.get('success'):
                error_message = data.get('error', 'Token inválido ou já utilizado.')
                await interaction.followup.send(f"❌ {error_message}", ephemeral=True)
                return

            user_data = data.get('data', {})
            
            if user_data.get('is_expired'):
                await interaction.followup.send("❌ Sua assinatura expirou. Por favor, renove para validar seu acesso.", ephemeral=True)
                return

            target_role_name = user_data.get('discord_role')
            if not target_role_name:
                await interaction.followup.send("❌ Erro: Não foi possível determinar seu cargo. Contate o suporte.", ephemeral=True)
                return
            
            guild = interaction.guild
            member = interaction.user
            role_to_add = discord.utils.get(guild.roles, name=target_role_name)

            if not role_to_add:
                await interaction.followup.send(f"❌ Erro crítico: O cargo '{target_role_name}' não foi encontrado. Contate um administrador.", ephemeral=True)
                return

            roles_to_remove = [role for role in member.roles if role.name in ROLE_MAP.values()]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Ajuste de plano de assinatura")

            await member.add_roles(role_to_add, reason="Validação de assinatura via site")

            # 4. Chamar a API para marcar como validado (com autenticação)
            post_data = {'code': token, 'discord_user_id': str(member.id)}
            requests.post(f"{API_BASE_URL}?action=mark_validated", json=post_data, headers=headers)

            await interaction.followup.send(f"✅ Validação concluída! Você recebeu o cargo **{target_role_name}**. Bem-vindo(a)!", ephemeral=True)

        except requests.exceptions.RequestException as e:
            print(f"Erro de API na validação: {e}")
            await interaction.followup.send("❌ Ocorreu um erro ao comunicar com nosso sistema. Tente novamente mais tarde.", ephemeral=True)
        except Exception as e:
            print(f"Erro inesperado na validação: {e}")
            await interaction.followup.send("❌ Ocorreu um erro inesperado. Contate o suporte.", ephemeral=True)


# --- VIEW: A CAIXA COM OS BOTÕES DE VALIDAÇÃO ---
class ValidationView(ui.View):
    # (Esta classe não precisa de alterações)
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✅ Validar", style=discord.ButtonStyle.green, custom_id="persistent_validation_button")
    async def validate_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ValidationModal())

    @ui.button(label="📩 Ainda não sou aluno", style=discord.ButtonStyle.blurple, custom_id="persistent_register_button")
    async def register_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(f"Para se tornar um aluno e obter seu token, [clique aqui]({REGISTRATION_LINK}).", ephemeral=True)

# --- CONEXÃO COM O BOT ---
intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# --- TAREFA AGENDADA: VERIFICAÇÃO DE ASSINATURAS EXPIRADAS ---
# Loop alterado para rodar a cada 1 hora
@tasks.loop(hours=1)
async def check_expired_subscriptions():
    print("Iniciando verificação de assinaturas expiradas...")
    headers = {'X-API-Key': API_KEY}

    try:
        params = {'action': 'get_expired_users'}
        response = requests.get(API_BASE_URL, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get('success'):
            expired_users = data.get('expired_users', [])
            print(f"Encontrados {len(expired_users)} usuários expirados.")
            
            for user in expired_users:
                discord_id = user.get('discord_user_id')
                tier = user.get('subscription_tier')
                if not discord_id or not tier:
                    continue

                for guild in client.guilds:
                    member = guild.get_member(int(discord_id))
                    role_to_remove = discord.utils.get(guild.roles, name=ROLE_MAP.get(tier))
                    
                    if member and role_to_remove and role_to_remove in member.roles:
                        await member.remove_roles(role_to_remove, reason="Assinatura expirada")
                        print(f"Cargo '{role_to_remove.name}' removido de {member.name} (ID: {discord_id}).")
                        try:
                            await member.send(
                                "Olá! Notamos que sua assinatura da TradingClass expirou. "
                                f"Seu cargo exclusivo foi removido. Para renovar e continuar com acesso, visite nosso site: {REGISTRATION_LINK}"
                            )
                        except discord.Forbidden:
                            print(f"Não foi possível enviar DM para {member.name}.")

    except requests.exceptions.RequestException as e:
        print(f"Erro de API na verificação de expirados: {e}")
    except Exception as e:
        print(f"Erro inesperado na verificação de expirados: {e}")

# --- EVENTO DE BOT PRONTO ---
@client.event
async def on_ready():
    client.add_view(ValidationView())
    if not check_expired_subscriptions.is_running():
        check_expired_subscriptions.start()
    await tree.sync()
    print(f'✅ Bot {client.user} está online e pronto!')

# --- COMANDOS DE SETUP (NÃO MUDARAM) ---
# (Todos os comandos como /enviar_painel_validacao, /regras, etc. continuam iguais)
@tree.command(name="enviar_painel_validacao", description="Envia o painel de validação fixo neste canal.")
@app_commands.default_permissions(administrator=True)
async def send_validation_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔑 Área Exclusiva para Alunos TradingClass",
        description="Clique no botão abaixo para inserir seu TOKEN único e liberar seu cargo:",
        color=EMBED_COLOR
    )
    await interaction.channel.send(embed=embed, view=ValidationView())
    await interaction.response.send_message("Painel de validação enviado!", ephemeral=True)

@tree.command(name="enviar_boas_vindas", description="Envia a mensagem de boas-vindas neste canal.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(canal_validacao="O canal para onde o botão de validação deve levar.")
async def send_welcome_message(interaction: discord.Interaction, canal_validacao: discord.TextChannel):
    welcome_text = (
        "💎 **COMUNIDADE TRADINGCLASS**\n\n"
        "Este é um espaço exclusivo da OpereBem para quem decidiu evoluir de verdade no mercado...\n"
    ) # (Texto completo omitido para brevidade)
    embed = discord.Embed(description=welcome_text, color=EMBED_COLOR)
    view = ui.View()
    view.add_item(ui.Button(label="Ir para Validação", style=discord.ButtonStyle.link, url=canal_validacao.jump_url))
    view.add_item(ui.Button(label="Ainda não sou aluno", style=discord.ButtonStyle.link, url=REGISTRATION_LINK))
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("Mensagem de boas-vindas enviada!", ephemeral=True)

@tree.command(name="regras", description="Envia a mensagem com as regras da comunidade neste canal.")
@app_commands.default_permissions(administrator=True)
async def send_rules(interaction: discord.Interaction):
    rules_text = (
        "1️⃣ **Respeito em primeiro lugar**\n"
        "Trate todos com cordialidade...\n\n" # (Texto completo omitido para brevidade)
        "✅ Ao utilizar a comunidade, você declara que leu e concorda com os Termos de Uso da TradingClass."
    )
    embed = discord.Embed(title="📜 Regras da Comunidade TradingClass", description=rules_text, color=EMBED_COLOR)
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("Mensagem de regras enviada!", ephemeral=True)

# --- RODAR O BOT ---
bot_token = os.environ.get('DISCORD_TOKEN')
api_url = os.environ.get('API_BASE_URL')
api_key = os.environ.get('API_KEY')
# Verificação da API_KEY ao iniciar
if not bot_token or not api_url or not api_key:
    print("❌ Erro crítico: DISCORD_TOKEN, API_BASE_URL ou API_KEY não foram encontrados nas variáveis de ambiente.")
else:
    client.run(bot_token)
