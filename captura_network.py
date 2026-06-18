# Abre o site da Receita no Playwright e captura TUDO da aba Network.
# Você preenche manualmente. Quando terminar, feche o browser.
# O log completo é salvo em network_capture.json

import sys, asyncio, json, time
from playwright.async_api import async_playwright

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("cp"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL = "https://servicos.receita.fazenda.gov.br/servicos/cpf/consultasituacao/consultapublica.asp"
OUTPUT = "network_capture.json"

requests_log = []

def on_request(request):
    entry = {
        "timestamp": time.time(),
        "direction": "REQUEST",
        "method": request.method,
        "url": request.url,
        "resource_type": request.resource_type,
        "headers": dict(request.headers),
        "post_data": request.post_data[:2000] if request.post_data else None,
    }
    requests_log.append(entry)
    tipo = request.resource_type
    url_short = request.url[:100]
    print(f"  ➡ {request.method} [{tipo}] {url_short}")

def on_response(response):
    entry = {
        "timestamp": time.time(),
        "direction": "RESPONSE",
        "status": response.status,
        "url": response.url,
        "headers": dict(response.headers),
    }
    requests_log.append(entry)
    url_short = response.url[:100]
    print(f"  ⬅ {response.status} {url_short}")

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(locale="pt-BR")
        page = await context.new_page()

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"  🌐 Abrindo {URL}")
        print("  📝 Preencha tudo manualmente e submeta o formulário.")
        print("  ❌ Quando terminar, FECHE o browser.\n")

        await page.goto(URL, wait_until="domcontentloaded")

        try:
            await page.wait_for_event("close", timeout=0)
        except Exception:
            pass

        await context.close()
        await browser.close()

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(requests_log, f, indent=2, ensure_ascii=False)

    print(f"\n  ✅ Capturado {len(requests_log)} eventos → {OUTPUT}")

asyncio.run(main())
