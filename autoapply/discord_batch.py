"""Owner-only review controls. No Discord button submits an employer form."""
import discord


class BatchView(discord.ui.View):
    def __init__(self, bot, application_ids):
        super().__init__(timeout=3600)
        self.bot = bot
        for app_id in application_ids[:5]:
            self.add_command(f'Review #{app_id}', f'review {app_id}')
        self.add_command('Lock Submit / refill', 'lock')
        self.add_command('Next five', 'next')

    def add_command(self, label, command):
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
        async def callback(interaction):
            if not self.bot.authorized(interaction.user):
                await interaction.response.send_message('Unauthorized',ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                result = await self.bot.controller.semi_auto.command(command)
            except (ValueError, RuntimeError) as exc:
                result = str(exc)
            await interaction.followup.send(result,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        button.callback = callback
        self.add_item(button)
