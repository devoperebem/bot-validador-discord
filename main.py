import discord
from discord import app_commands, ui
import os
import mysql.connector
from mysql.connector import Error

# --- CONFIGURAÇÃO ---
# MAPA DE CARGOS SIMPLIFICADO AQUI
ROLE_MAP = {
    'Aluno': 'Aluno',
    'Mentorado': 'Mentorado'
}
REGISTRATION_LINK = "https://aluno.operebem.com.br"
EMBED_COLOR = 0x5865F2 

# --- MODAL: O FORMULÁRIO POP-UP PARA O CÓDIGO ---
class ValidationModal(ui.Modal, title="Validação de Acesso"):
    token_input = ui.TextInput(label="Seu Token de Validação", placeholder="Cole aqui o token que você pegou no site...", style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        # A lógica interna de validação permanece a mesma, agora usando o novo ROLE_MAP
        await interaction.response.defer(ephemeral=True, thinking=True)
        token = self.token_input.value.strip()
        connection = create_db_connection()

        if not connection:
            await interaction.followup.send("❌ Ocorreu um erro interno. A conexão com o banco de dados falhou.", ephemeral=True)
            return

        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM discord_validation WHERE validation_code = %s", (token,))
            record = cursor.fetchone()

            if not record:
                await interaction.followup.send("❌ Token inválido. Verifique o código e tente novamente.", ephemeral=True)
                return

            if record['is_validated']:
                await interaction.followup.send("⚠️ Este token já foi utilizado.", ephemeral=True)
                return

            target_tier = record['subscription_tier']
            target_role_name = ROLE_MAP.get(target_tier)

            if not target_role_name:
                await interaction.followup.send(f"❌ Erro: Seu plano '{target_tier}' não corresponde a um cargo válido. Contate o suporte.", ephemeral=True)
                return

            guild = interaction.guild
            member = interaction.user
            role_to_add = discord.utils.get(guild.roles, name=target_role_name)

            if not role_to_add:
                await interaction.followup.send(f"❌ Erro crítico: O cargo '{target_role_name}' não foi encontrado. Contate um administrador.", ephemeral=True)
                return

            roles_to_remove = [role for role in member.roles if role.name in ROLE_MAP.values() and role.name != target_role_name]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Upgrade ou ajuste de plano")

            await member.add_roles(role_to_add, reason="Validação de assinatura via site")

            cursor.execute("UPDATE discord_validation SET is_validated = TRUE, discord_user_id = %s WHERE id = %s", (str(member.id), record['id']))
            connection.commit()

            await interaction.followup.send(f"✅ Validação concluída! Você recebeu o cargo **{target_role_name}**. Bem-vindo(a)!", ephemeral=True)

        except Error as e:
            print(f"Erro de banco de dados na validação: {e}")
            await interaction.followup.send("❌ Ocorreu um erro ao processar sua validação. Tente novamente.", ephemeral=True)
        finally:
            cursor.close()
            connection.close()


# --- VIEW: A CAIXA COM OS BOTÕES DE VALIDAÇÃO ---
class ValidationView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✅ Validar", style=discord.ButtonStyle.green, custom_id="persistent_validation_button")
    async def validate_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ValidationModal())

    @ui.button(label="📩 Ainda não sou aluno", style=discord.ButtonStyle.blurple, custom_id="persistent_register_button")
    async def register_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            f"Para se tornar um aluno e obter seu token de acesso, [clique aqui para se cadastrar]({REGISTRATION_LINK}).",
            ephemeral=True
        )

# --- CONEXÃO COM O BOT ---
intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# --- CONEXÃO COM O BANCO DE DADOS ---
def create_db_connection():
    try:
        connection = mysql.connector.connect(
            host=os.environ.get('DB_HOST'),
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
            database=os.environ.get('DB_NAME')
        )
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"Erro ao conectar ao MySQL: {e}")
        return None

# --- EVENTO DE BOT PRONTO (LÓGICA DE PERSISTÊNCIA) ---
@client.event
async def on_ready():
    client.add_view(ValidationView())
    await tree.sync()
    print(f'✅ Bot {client.user} está online e pronto!')
    print('Comandos sincronizados.')

# --- COMANDO DE SETUP DO PAINEL DE VALIDAÇÃO ---
@tree.command(name="enviar_painel_validacao", description="Envia o painel de validação fixo neste canal.")
@app_commands.default_permissions(administrator=True)
async def send_validation_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔑 Área Exclusiva para Alunos TradingClass",
        description="Para acessar os canais e benefícios exclusivos, é necessário validar seu cadastro.\n\nClique no botão abaixo para inserir seu TOKEN único (disponível na área do aluno) e liberar automaticamente seu cargo:",
        color=EMBED_COLOR
    )
    await interaction.channel.send(embed=embed, view=ValidationView())
    await interaction.response.send_message("Painel de validação enviado!", ephemeral=True)

# --- COMANDO DE BOAS-VINDAS ---
@tree.command(name="enviar_boas_vindas", description="Envia a mensagem de boas-vindas neste canal.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(canal_validacao="O canal para onde o botão de validação deve levar.")
async def send_welcome_message(interaction: discord.Interaction, canal_validacao: discord.TextChannel):
    welcome_text = (
        # BEM-VINDO ALTERADO AQUI
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

    embed = discord.Embed(
        description=welcome_text,
        color=EMBED_COLOR
    )

    view = ui.View()
    view.add_item(ui.Button(label="Ir para Validação", style=discord.ButtonStyle.link, url=canal_validacao.jump_url))
    view.add_item(ui.Button(label="Ainda não sou aluno", style=discord.ButtonStyle.link, url=REGISTRATION_LINK))

    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("Mensagem de boas-vindas enviada!", ephemeral=True)

# --- NOVO COMANDO DE REGRAS ---
@tree.command(name="regras", description="Envia a mensagem com as regras da comunidade neste canal.")
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

    embed = discord.Embed(
        title="📜 Regras da Comunidade TradingClass",
        description=rules_text,
        color=EMBED_COLOR
    )

    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("Mensagem de regras enviada!", ephemeral=True)

# --- RODAR O BOT ---
bot_token = os.environ.get('DISCORD_TOKEN')
if not bot_token:
    print("❌ Erro crítico: O DISCORD_TOKEN não foi encontrado nas variáveis de ambiente.")
else:
    client.run(bot_token)
