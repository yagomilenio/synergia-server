import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import config


def _send(to: str, subject: str, html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = config.SMTP_FROM
    msg["To"]      = to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWD)
        smtp.sendmail(config.SMTP_USER, to, msg.as_string())


def send_verification_email(to: str, username: str, token: str, base_url: str):
    link = f"{base_url}/verify-email?token={token}"
    html = f"""
    <div style="font-family:monospace;max-width:480px;margin:auto;padding:32px;background:#0b0d14;color:#e2e8f5;border:1px solid #252c42;border-radius:10px">
      <h2 style="color:#00e5a0;margin-bottom:8px">⬡ Synergia</h2>
      <p style="color:#8892b0;margin-bottom:24px">Verificación de cuenta</p>
      <p>Hola <strong>{username}</strong>,</p>
      <p>Confirma tu dirección de email haciendo clic en el botón:</p>
      <a href="{link}"
         style="display:inline-block;margin:24px 0;padding:12px 28px;background:#00e5a0;color:#0b0d14;font-weight:700;border-radius:6px;text-decoration:none">
        Verificar email
      </a>
      <p style="color:#4a5378;font-size:12px">O copia este enlace en tu navegador:<br>{link}</p>
      <p style="color:#4a5378;font-size:12px;margin-top:24px">Este enlace expira en 24 horas.</p>
    </div>
    """
    _send(to, "Verifica tu cuenta en Synergia", html)