# pip3 install anticaptchaofficial requests beautifulsoup4 python-dotenv

import sys, os, requests, time, threading, csv, json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from anticaptchaofficial.hcaptchaproxyless import hCaptchaProxyless
from bs4 import BeautifulSoup
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("cp"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

ANTICAPTCHA_API_KEY = os.environ["ANTICAPTCHA_API_KEY"]
HCAPTCHA_SITE_KEY   = os.environ["HCAPTCHA_SITE_KEY"]
URL_FORM            = "https://servicos.receita.fazenda.gov.br/servicos/cpf/consultasituacao/consultapublica.asp"
URL_POST            = "https://servicos.receita.fazenda.gov.br/servicos/cpf/consultasituacao/ConsultaPublicaExibir.asp"
WORKERS             = 3
NETWORK_LOG_FILE    = "network_log.json"
_network_log        = []

def log_request(label: str, resp: requests.Response, payload: dict | None = None):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "label": label,
        "request": {
            "method": resp.request.method,
            "url": resp.request.url,
            "headers": dict(resp.request.headers),
            "body": resp.request.body if resp.request.body else None,
        },
        "response": {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "cookies": dict(resp.cookies),
            "encoding": resp.encoding,
            "content_length": len(resp.content),
        },
        "session_cookies": {c.name: c.value for c in resp.cookies},
    }
    if payload:
        entry["request"]["payload_fields"] = {k: v[:80] if len(v) > 80 else v for k, v in payload.items()}
    _network_log.append(entry)

def salvar_network_log():
    with open(NETWORK_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(_network_log, f, indent=2, ensure_ascii=False)
    print(f"  📋 Network log salvo em {NETWORK_LOG_FILE} ({len(_network_log)} requests)")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

HEADERS_GET = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": USER_AGENT,
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

HEADERS_POST = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://servicos.receita.fazenda.gov.br",
    "Referer": URL_FORM,
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": USER_AGENT,
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# ──────────────────────────────────────────
# RESOLVER CAPTCHA
# ──────────────────────────────────────────
def resolver_captcha() -> str:
    print("  🔑 Resolvendo hCaptcha...")
    t0 = time.time()
    solver = hCaptchaProxyless()
    solver.set_verbose(0)
    solver.set_key(ANTICAPTCHA_API_KEY)
    solver.set_website_url(URL_FORM)
    solver.set_website_key(HCAPTCHA_SITE_KEY)
    solver.set_user_agent(USER_AGENT)
    token = solver.solve_and_return_solution()
    if token == 0:
        raise RuntimeError(f"hCaptcha falhou: {solver.error_code} — {solver.err_string}")
    print(f"  🔑 Token resolvido em {time.time()-t0:.1f}s | {token[:50]}...")
    return token

# ──────────────────────────────────────────
# CONSULTA CPF
# ──────────────────────────────────────────
def consultar_cpf(cpf: str, nascimento: str, tentativas: int = 5) -> dict | None:
    cpf_limpo = cpf.replace(".", "").replace("-", "")
    cpf_fmt   = f"{cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}"

    for t in range(tentativas):
        try:
            token = resolver_captcha()

            session = requests.Session()
            session.headers.update(HEADERS_GET)
            r = session.get(URL_FORM, timeout=20)
            r.raise_for_status()
            log_request(f"GET_form_t{t+1}", r)
            print(f"  🍪 Sessão iniciada | cookies: {dict(session.cookies)}")

            session.headers.update(HEADERS_POST)
            payload = {
                "idCheckedReCaptcha": "true",
                "txtCPF":             cpf_fmt,
                "txtDataNascimento":  nascimento,
                "h-captcha-response": token,
                "g-recaptcha-response": token,
                "Enviar":             "Consultar",
            }
            r = session.post(URL_POST, data=payload, timeout=30)
            r.raise_for_status()
            r.encoding = "iso-8859-1"
            log_request(f"POST_consulta_t{t+1}", r, payload)

            resultado = parsear_resposta(r.text, cpf_fmt)

            if resultado is None:
                print(f"  ⚠ Resposta inválida para CPF {cpf_fmt}, tentativa {t+1}")
                time.sleep(2)
                continue

            return resultado

        except Exception as e:
            print(f"  ❌ Erro CPF {cpf_fmt}: {e}")
            time.sleep(2)

    return None

def parsear_resposta(html: str, cpf: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")

    if not soup.find("span", class_="clConteudoDados"):
        erro = soup.find("span", class_="mensagemErro")
        if erro:
            print(f"  ℹ Motivo: {erro.get_text(strip=True)}")
        return None

    resultado = {}

    for span in soup.find_all("span", class_="clConteudoDados"):
        bold = span.find("b")
        if not bold:
            continue
        valor = bold.get_text(strip=True)
        bold.extract()
        label = span.get_text(strip=True).rstrip(":").strip()
        chave = (
            label
            .replace("No do CPF", "CPF")
            .replace("Nº do CPF", "CPF")
            .replace("N° do CPF", "CPF")
            .replace("Situação Cadastral", "SITUACAO")
            .replace("Data de Nascimento", "DT_NASCIMENTO")
            .replace("Data da Inscrição", "DT_INSCRICAO")
            .replace("Digito Verificador", "DIGITO_VERIFICADOR")
            .replace("Nome", "NOME")
            .replace(" ", "_")
            .upper()
        )
        resultado[chave] = valor

    for span in soup.find_all("span", class_="clConteudoComp"):
        bolds = span.find_all("b")
        texto = span.get_text(strip=True)
        if "emitido" in texto and len(bolds) >= 2:
            resultado["HORA_EMISSAO"] = bolds[0].get_text(strip=True)
            resultado["DT_EMISSAO"]   = bolds[1].get_text(strip=True)
        elif "controle" in texto.lower() and bolds:
            resultado["COD_CONTROLE"] = bolds[0].get_text(strip=True)

    if "CPF" not in resultado:
        resultado["CPF"] = cpf

    return resultado if len(resultado) > 1 else None

# ──────────────────────────────────────────
# PROCESSAMENTO EM LOTE
# ──────────────────────────────────────────
def processar_lote(lista_cpf_nasc: list[tuple[str, str]], arquivo_saida: str = "resultado_cpf.csv"):
    ja_feitos = set()
    if os.path.exists(arquivo_saida):
        with open(arquivo_saida, encoding="utf-8-sig") as f:
            ja_feitos = {row["CPF"] for row in csv.DictReader(f) if row.get("CPF")}
        print(f"  ♻ Checkpoint: {len(ja_feitos)} CPFs já processados")

    pendentes = [(c, n) for c, n in lista_cpf_nasc
                 if c.replace(".", "").replace("-", "") not in
                    {x.replace(".", "").replace("-", "") for x in ja_feitos}]

    print(f"  📋 {len(pendentes)} CPFs pendentes de {len(lista_cpf_nasc)} total\n")
    if not pendentes:
        return

    modo       = "a" if ja_feitos else "w"
    resultados = []
    lock       = threading.Lock()
    concluidos = [0]
    inicio     = time.time()

    def tarefa(cpf, nasc):
        res = consultar_cpf(cpf, nasc)
        with lock:
            concluidos[0] += 1
            n   = concluidos[0]
            pct = n / len(pendentes) * 100
            eta = ((time.time() - inicio) / n) * (len(pendentes) - n) if n else 0
            sit = res.get("SITUACAO", "⚠ SEM RESULTADO") if res else "❌ FALHA"
            print(f"  [{n}/{len(pendentes)}] {pct:.1f}% | ETA {eta/60:.1f}min | {cpf} → {sit}")
            if res:
                resultados.append(res)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(tarefa, c, n) for c, n in pendentes]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                print(f"  ❌ Worker erro: {e}")

    if resultados:
        campos = list({k for r in resultados for k in r})
        with open(arquivo_saida, modo, newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            if modo == "w":
                w.writeheader()
            w.writerows(resultados)
        print(f"\n✅ Salvo em {arquivo_saida}")

# ──────────────────────────────────────────
# EXEMPLO DE USO
# ──────────────────────────────────────────
if __name__ == "__main__":
    resultado = consultar_cpf("109.470.846-14", "12/04/1999")
    print(resultado)
    salvar_network_log()

    # import csv

    # def normalizar_cpf(cpf: str) -> str:
    #     c = cpf.strip().replace(".", "").replace("-", "").replace(" ", "")
    #     return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"

    # with open("entrada.csv", encoding="utf-8-sig") as f:
    #     lista = [(normalizar_cpf(r["cpf"]), r["nascimento"].strip()) for r in csv.DictReader(f)]

    # processar_lote(lista)
