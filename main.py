import discord
from discord import app_commands, ui
from discord.ext import tasks
import os
import requests

# --- CONFIGURAÇÃO ---
# As configurações agora são carregadas da API no início
API_BASE_URL = os.environ.get('API_BASE_URL') 
API_KEY = os.environ.get('API_KEY')
GUILD_ID = int(os.environ.get('GUILD_ID'))

# IDs dos cargos serão carregados da API
ROLE_ALUNO_ID = None
ROLE_MENTORADO_ID = None

REGISTRATION_LINK = "https://aluno.operebem.com.br"
EMBED_COLOR = 0x5865F2

# --- MODAL: O FORMULÁRIO POP-UP PARA O CÓDIGO ---
class ValidationModal(ui.Modal, title="Validação de Acesso"):
    token_input = ui.TextInput(label="Seu Token de Validação", placeholder="Cole aqui o token que você pegou no site...", style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        token = self.token_input.value.strip()
        headers = {'X-API-Key': API_KEY}
        
        try:
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
            
            roles_to_remove_ids = [ROLE_ALUNO_ID, ROLE_MENTORADO_ID]
            roles_to_remove = [role for role in member.roles if role.id in roles_to_remove_ids]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Ajuste de plano de assinatura")

            await member.add_roles(role_to_add, reason="Validação de assinatura via site")

            post_data = {'code': token, 'discord_user_id': str(member.id), 'bot_user_id': str(client.user.id)}
            requests.post(f"{API_BASE_URL}?action=mark_validated", json=post_data, headers=headers)

            await interaction.followup.send(f"✅ Validação concluída! Você recebeu o cargo **{role_to_add.name}**. Bem-vindo(a)!", ephemeral=True)

        except requests.exceptions.RequestException as e:
            print(f"Erro de API na validação: {e}")
            await interaction.followup.send("❌ Ocorreu um erro ao comunicar com nosso sistema. Tente novamente mais tarde.", ephemeral=True)
        except Exception as e:
            print(f"Erro inesperado na validação: {e}")
            await interaction.followup.send("❌ Ocorreu um erro inesperado. Contate o suporte.", ephemeral=True)

# --- VIEW: A CAIXA COM OS BOTÕES DE VALIDAÇÃO ---
class ValidationView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✅ Validar", style=discord.ButtonStyle.green, custom_id="persistent_validation_button")
    async def validate_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ValidationModal())

    @ui.button(label="📩 Ainda não sou aluno", style=discord.ButtonStyle.blurple, custom_id="persistent_register_button")
    async def register_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(f"Para se tornar um aluno, [clique aqui]({REGISTRATION_LINK}).", ephemeral=True)

# --- CONEXÃO COM O BOT ---
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

# --- TAREFAS AGENDADAS ---
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
            guild = client.get_guild(GUILD_ID)
            if not guild: return
            
            for user in expired_users:
                discord_id = user.get('discord_user_id')
                tier = user.get('subscription_tier')
                if not discord_id or not tier: continue

                member = guild.get_member(int(discord_id))
                role_id_to_remove = ROLE_ALUNO_ID if tier == 'Aluno' else ROLE_MENTORADO_ID
                role_to_remove = guild.get_role(role_id_to_remove)
                
                if member and role_to_remove and role_to_remove in member.roles:
                    await member.remove_roles(role_to_remove, reason="Assinatura expirada")
                    
                    post_data = {'discord_user_id': str(discord_id), 'bot_user_id': str(client.user.id)}
                    requests.post(f"{API_BASE_URL}?action=mark_role_removed", json=post_data, headers=headers)

                    print(f"Cargo '{role_to_remove.name}' removido de {member.name}.")
                    try:
                        await member.send(f"Olá! Notamos que sua assinatura {tier} expirou. Seu cargo foi removido. Para renovar, visite: {REGISTRATION_LINK}")
                    except discord.Forbidden:
                        print(f"Não foi possível enviar DM para {member.name}.")

    except Exception as e:
        print(f"Erro na verificação de expirados: {e}")

@tasks.loop(hours=6)
async def sync_users():
    print("Iniciando sincronização de usuários pendentes...")
    headers = {'X-API-Key': API_KEY}
    try:
        params = {'action': 'get_sync_pending'}
        response = requests.get(API_BASE_URL, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get('success'):
            pending_users = data.get('users', [])
            print(f"Encontrados {len(pending_users)} usuários para sincronizar.")
            guild = client.get_guild(GUILD_ID)
            if not guild: return

            for user in pending_users:
                if user.get('subscription_status') != 'active': continue
                
                discord_id = user.get('discord_user_id')
                tier = user.get('subscription_tier')
                if not discord_id or not tier: continue

                member = guild.get_member(int(discord_id))
                role_id_to_add = ROLE_ALUNO_ID if tier == 'Aluno' else ROLE_MENTORADO_ID
                role_to_add = guild.get_role(role_id_to_add)

                if member and role_to_add and role_to_add not in member.roles:
                    await member.add_roles(role_to_add, reason="Sincronização de assinatura ativa")
                    print(f"Cargo '{role_to_add.name}' sincronizado para {member.name}.")

    except Exception as e:
        print(f"Erro na sincronização de usuários: {e}")

# --- EVENTO DE BOT PRONTO ---
@client.event
async def on_ready():
    global ROLE_ALUNO_ID, ROLE_MENTORADO_ID
    headers = {'X-API-Key': API_KEY}
    print("Carregando configurações da API...")
    try:
        params_aluno = {'action': 'get_config', 'key': 'role_aluno_id'}
        response_aluno = requests.get(API_BASE_URL, params=params_aluno, headers=headers).json()
        if response_aluno.get('success'):
            ROLE_ALUNO_ID = int(response_aluno['value'])
            print(f"ID do cargo Aluno carregado: {ROLE_ALUNO_ID}")

        params_mentorado = {'action': 'get_config', 'key': 'role_mentorado_id'}
        response_mentorado = requests.get(API_BASE_URL, params=params_mentorado, headers=headers).json()
        if response_mentorado.get('success'):
            ROLE_MENTORADO_ID = int(response_mentorado['value'])
            print(f"ID do cargo Mentorado carregado: {ROLE_MENTORADO_ID}")
    except Exception as e:
        print(f"ERRO CRÍTICO ao carregar IDs dos cargos da API: {e}")

    client.add_view(ValidationView())
    if not check_expired_subscriptions.is_running():
        check_expired_subscriptions.start()
    if not sync_users.is_running():
        sync_users.start()
        
    print(f'✅ Bot {client.user} está online e pronto!')

# --- COMANDOS ADMINISTRATIVOS ---
@client.tree.command(name="status", description="Verifica o status do sistema e da API.")
@app_commands.default_permissions(administrator=True)
async def status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    headers = {'X-API-Key': API_KEY}
    embed = discord.Embed(title="📊 Status do Sistema de Integração", color=EMBED_COLOR)
    
    try:
        response = requests.get(API_BASE_URL, params={'action':'list'}, headers=headers)
        if response.status_code == 200:
            embed.add_field(name="Conexão com a API", value="✅ Sucesso", inline=False)
            
            expired_data = requests.get(API_BASE_URL, params={'action':'get_expired_users'}, headers=headers).json()
            sync_data = requests.get(API_BASE_URL, params={'action':'get_sync_pending'}, headers=headers).json()

            embed.add_field(name="Usuários Expirados", value=expired_data.get('total', 'N/A'), inline=True)
            embed.add_field(name="Pendentes de Sincronização", value=sync_data.get('total', 'N/A'), inline=True)
        else:
            embed.add_field(name="Conexão com a API", value=f"❌ Falha (Código: {response.status_code})", inline=False)
    except Exception as e:
        embed.add_field(name="Conexão com a API", value=f"❌ Falha Grave: {e}", inline=False)

    embed.add_field(name="ID Cargo Aluno", value=f"`{ROLE_ALUNO_ID}`" if ROLE_ALUNO_ID else "Não configurado", inline=False)
    embed.add_field(name="ID Cargo Mentorado", value=f"`{ROLE_MENTORADO_ID}`" if ROLE_MENTORADO_ID else "Não configurado", inline=False)
    
    await interaction.followup.send(embed=embed)

@client.tree.command(name="configurar_cargos", description="Configura os IDs dos cargos Aluno e Mentorado.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(aluno="O cargo para membros Alunos.", mentorado="O cargo para membros Mentorados.")
async def configure_roles(interaction: discord.Interaction, aluno: discord.Role, mentorado: discord.Role):
    await interaction.response.defer(ephemeral=True)
    headers = {'X-API-Key': API_KEY}
    try:
        post_data_aluno = {'key': 'role_aluno_id', 'value': str(aluno.id)}
        requests.post(f"{API_BASE_URL}?action=update_config", json=post_data_aluno, headers=headers).raise_for_status()

        post_data_mentorado = {'key': 'role_mentorado_id', 'value': str(mentorado.id)}
        requests.post(f"{API_BASE_URL}?action=update_config", json=post_data_mentorado, headers=headers).raise_for_status()

        global ROLE_ALUNO_ID, ROLE_MENTORADO_ID
        ROLE_ALUNO_ID = aluno.id
        ROLE_MENTORADO_ID = mentorado.id

        await interaction.followup.send("✅ IDs dos cargos configurados com sucesso na API e no bot!")
    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao atualizar configuração na API: {e}")

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

# --- RODAR O BOT ---
bot_token = os.environ.get('DISCORD_TOKEN')
if not bot_token or not API_BASE_URL or not API_KEY or not GUILD_ID:
    print("❌ Erro crítico: Uma ou mais variáveis de ambiente (DISCORD_TOKEN, API_BASE_URL, API_KEY, GUILD_ID) não foram encontradas.")
else:
    client.run(bot_token)
