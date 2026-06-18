# pip3 install anticaptchaofficial requests beautifulsoup4

import requests, time, threading, csv, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from anticaptchaofficial.hcaptchaproxyless import hCaptchaProxyless
from bs4 import BeautifulSoup

ANTICAPTCHA_API_KEY = "97c0e2a3b8af934729c1123ce73f5f48"
HCAPTCHA_SITE_KEY   = "53be2ee7-5efc-494e-a3ba-c9258649c070"
URL_FORM            = "https://servicos.receita.fazenda.gov.br/servicos/cpf/consultasituacao/consultapublica.asp"
URL_POST            = "https://servicos.receita.fazenda.gov.br/servicos/cpf/consultasituacao/ConsultaPublicaExibir.asp"
TOKEN_TTL_SEGUNDOS  = 90   # hCaptcha expira mais rápido que reCaptcha
WORKERS             = 3    # conservador — site gov, evitar bloqueio

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://servicos.receita.fazenda.gov.br",
    "Referer": URL_FORM,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Upgrade-Insecure-Requests": "1",
}

# ──────────────────────────────────────────
# SESSÃO — cookies ASPSESSIONID são obrigatórios
# ──────────────────────────────────────────
def nova_sessao() -> requests.Session:
    """Faz um GET na página do formulário para obter os cookies de sessão."""
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.get(URL_FORM, timeout=20)
    r.raise_for_status()
    print(f"  🍪 Sessão iniciada | cookies: {dict(s.cookies)}")
    return s

# ──────────────────────────────────────────
# TOKEN hCaptcha compartilhado entre threads
# ──────────────────────────────────────────
class CaptchaToken:
    def __init__(self):
        self._token   = ""
        self._ts      = 0.0
        self._lock    = threading.Lock()
        self._session = None

    def get(self) -> tuple[str, requests.Session]:
        """Retorna (token, session). Renova ambos se necessário."""
        with self._lock:
            if not self._token or (time.time() - self._ts) > TOKEN_TTL_SEGUNDOS:
                self._renovar()
            return self._token, self._session

    def invalidar(self):
        with self._lock:
            print("  ⚠ Token/sessão invalidados, serão renovados na próxima requisição.")
            self._token   = ""
            self._session = None

    def _renovar(self):
        print("\n  🔑 Resolvendo hCaptcha...")
        t0 = time.time()

        # ① Sessão primeiro — enquanto o captcha resolve, a sessão já está pronta
        nova_sess = nova_sessao()

        solver = hCaptchaProxyless()
        solver.set_verbose(0)
        solver.set_key(ANTICAPTCHA_API_KEY)
        solver.set_website_url(URL_FORM)
        solver.set_website_key(HCAPTCHA_SITE_KEY)
        token = solver.solve_and_return_solution()
        if token == 0:
            raise RuntimeError(f"hCaptcha falhou: {solver.error_code}")

        # ② Token atribuído imediatamente após resolução
        self._token   = token
        self._ts      = time.time()
        self._session = nova_sess
        print(f"  🔑 Token resolvido em {time.time()-t0:.1f}s | {token[:50]}...\n")

captcha = CaptchaToken()

# ──────────────────────────────────────────
# CONSULTA CPF
# ──────────────────────────────────────────
def consultar_cpf(cpf: str, nascimento: str, tentativas: int = 3) -> dict | None:
    cpf_limpo = cpf.replace(".", "").replace("-", "")
    cpf_fmt   = f"{cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}"

    for t in range(tentativas):
        token, session = captcha.get()
        payload = {
            "idCheckedReCaptcha": "false",
            "txtCPF":             cpf_fmt,
            "txtDataNascimento":  nascimento,
            "h-captcha-response": token,
            "Enviar":             "Consultar",
        }
        try:
            r = session.post(URL_POST, data=payload, timeout=30)
            r.raise_for_status()
            r.encoding = "iso-8859-1"   # ← adicionar esta linha

            # ── DEBUG: salva o HTML para inspecionar ──────────────────
            with open(f"debug_tentativa_{t+1}.html", "w", encoding="utf-8", errors="replace") as f:
                f.write(r.text)
            print(f"  🔍 HTML salvo em debug_tentativa_{t+1}.html ({len(r.text)} bytes)")
            # ─────────────────────────────────────────────────────────

            resultado = parsear_resposta(r.text, cpf_fmt)

            if resultado is None:
                print(f"  ⚠ Resposta inválida para CPF {cpf_fmt}, tentativa {t+1}")
                captcha.invalidar()
                time.sleep((t + 1) * 5)
                continue

            return resultado

        except Exception as e:
            print(f"  ❌ Erro CPF {cpf_fmt}: {e}")
            captcha.invalidar()
            time.sleep((t + 1) * 5)

    return None

def parsear_resposta(html: str, cpf: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")

    # Rejeição: captcha inválido → site devolve o formulário
    if not soup.find("span", class_="clConteudoDados"):
        erro = soup.find("span", class_="mensagemErro")
        if erro:
            print(f"  ℹ Motivo: {erro.get_text(strip=True)}")
        return None

    resultado = {}

    # ── Campos principais (clConteudoDados) ──────────────────────────────────
    # Estrutura: <span class="clConteudoDados">Label: <b>VALOR</b></span>
    for span in soup.find_all("span", class_="clConteudoDados"):
        bold = span.find("b")
        if not bold:
            continue

        valor  = bold.get_text(strip=True)
        # Remove o bold do span para isolar o label
        bold.extract()
        label  = span.get_text(strip=True).rstrip(":").strip()
        # Normaliza label para chave de dicionário
        chave  = (
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

    # ── Metadados do comprovante (clConteudoComp) ────────────────────────────
    # "Comprovante emitido às: 15:51:19 do dia 28/05/2026 (hora e data de Brasília)."
    # "Código de controle do comprovante: FBB4.8133.EC8F.D915"
    for span in soup.find_all("span", class_="clConteudoComp"):
        bolds = span.find_all("b")
        texto = span.get_text(strip=True)

        if "emitido" in texto and len(bolds) >= 2:
            resultado["HORA_EMISSAO"] = bolds[0].get_text(strip=True)
            resultado["DT_EMISSAO"]   = bolds[1].get_text(strip=True)

        elif "controle" in texto.lower() and bolds:
            resultado["COD_CONTROLE"] = bolds[0].get_text(strip=True)

    # ── Garante que o CPF consultado sempre conste ───────────────────────────
    if "CPF" not in resultado:
        resultado["CPF"] = cpf

    return resultado if len(resultado) > 1 else None

# ──────────────────────────────────────────
# PROCESSAMENTO EM LOTE
# ──────────────────────────────────────────
def processar_lote(lista_cpf_nasc: list[tuple[str, str]], arquivo_saida: str = "resultado_cpf.csv"):
    """
    lista_cpf_nasc: [(cpf, nascimento), ...]
    """
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

    modo      = "a" if ja_feitos else "w"
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
            sit = res.get("Situacao", "⚠ SEM RESULTADO") if res else "❌ FALHA"
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
    # Consulta única
    resultado = consultar_cpf("109.470.846-14", "12/04/1999")
    print(resultado)

    # import csv

    # def normalizar_cpf(cpf: str) -> str:
    #     c = cpf.strip().replace(".", "").replace("-", "").replace(" ", "")
    #     return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"

    # with open("entrada.csv", encoding="utf-8-sig") as f:
    #     lista = [(normalizar_cpf(r["cpf"]), r["nascimento"].strip()) for r in csv.DictReader(f)]

    # processar_lote(lista)