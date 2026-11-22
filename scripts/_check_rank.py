import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client

c = Client()
r = c.get("/partials/signals/?tab=all")
text = r.content.decode("utf-8", "ignore")
print("status", r.status_code)
print("score-bar", "score-bar" in text)
print("Showing", "Showing" in text)
print("cards", text.count('class="card ticket-'))
print("ESTIMATE strong", text.count("ESTIMATE"))
print("rank-pill", text.count("rank-pill"))
