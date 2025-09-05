import discord
from discord import app_commands
import os
import mysql.connector
from mysql.connector import Error

# --- CONFIGURAÇÃO ---
# Dicionário mapeando o tier do banco de dados para os nomes EXATOS dos cargos
ROLE_MAP = {
    'Prata': "Prata 🥈",
    'Ouro': "Ouro 🥇",
    'Diamante': "Diamante 💎"
}

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

# --- EVENTO DE BOT PRONTO ---
@client.event
async def on_ready():
    await tree.sync()
    print(f'✅ Bot {client.user} está online e pronto!')
    print('Comandos sincronizados.')

# --- COMANDO DE VALIDAÇÃO ---
@tree.command(name="validar", description="Valide sua assinatura e receba seu cargo exclusivo.")
@app_commands.describe(codigo="Seu código de validação gerado em nosso site.")
async def validar(interaction: discord.Interaction, codigo: str):
    await interaction.response.defer(ephemeral=True)

    connection = create_db_connection()
    if not connection:
        await interaction.followup.send("❌ Ocorreu um erro interno. A conexão com o banco de dados falhou.", ephemeral=True)
        return

    cursor = connection.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM discord_validation WHERE validation_code = %s", (codigo,))
        record = cursor.fetchone()

        if not record:
            await interaction.followup.send("❌ Código de validação inválido. Verifique o código e tente novamente.", ephemeral=True)
            return

        if record['is_validated']:
            await interaction.followup.send("⚠️ Este código já foi utilizado.", ephemeral=True)
            return
            
        target_tier = record['subscription_tier']
        target_role_name = ROLE_MAP.get(target_tier)
        
        if not target_role_name:
            await interaction.followup.send("❌ Erro: O seu plano não corresponde a um cargo válido. Contate o suporte.", ephemeral=True)
            return

        guild = interaction.guild
        member = interaction.user
        
        role_to_add = discord.utils.get(guild.roles, name=target_role_name)
        if not role_to_add:
            await interaction.followup.send(f"❌ Erro crítico: O cargo '{target_role_name}' não foi encontrado. Contate um administrador.", ephemeral=True)
            return
            
        roles_to_remove = []
        all_tier_roles = list(ROLE_MAP.values())
        for user_role in member.roles:
            if user_role.name in all_tier_roles and user_role.name != target_role_name:
                roles_to_remove.append(user_role)
        
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Upgrade de plano de assinatura")

        await member.add_roles(role_to_add, reason="Validação de assinatura via site")

        cursor.execute(
            "UPDATE discord_validation SET is_validated = TRUE, discord_user_id = %s WHERE id = %s", 
            (str(member.id), record['id'])
        )
        connection.commit()

        await interaction.followup.send(f"✅ Validação concluída! Você recebeu o cargo **{target_role_name}**. Bem-vindo(a)!", ephemeral=True)

    except Error as e:
        print(f"Erro de banco de dados durante a validação: {e}")
        await interaction.followup.send("❌ Ocorreu um erro ao processar sua validação. Tente novamente.", ephemeral=True)
    finally:
        cursor.close()
        connection.close()

# --- RODAR O BOT ---
bot_token = os.environ.get('DISCORD_TOKEN')
if not bot_token:
    print("❌ Erro crítico: O DISCORD_TOKEN não foi encontrado nas variáveis de ambiente.")
else:
    client.run(bot_token)
