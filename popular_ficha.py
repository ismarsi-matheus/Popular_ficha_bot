"""
Popula as abas da ficha (Peritagem, Checklist...) com dados aleatórios, descobrindo os campos sozinho.
Link, abas e botão de salvar ficam no config.json.
 
Instalação (uma vez):
    pip install playwright
    playwright install chromium
 
Uso:
    python popular_ficha.py --login                  # 1ª vez: loga no sistema, sessão fica salva
    python popular_ficha.py --mapear                 # só lista os campos de cada aba (campos_encontrados.csv)
    python popular_ficha.py                          # preenche a ordem 6832 e NÃO salva (para conferir)
    python popular_ficha.py --salvar                 # preenche e clica em Salvar
    python popular_ficha.py --ordens 6832 6833 6840 --salvar
    python popular_ficha.py --de 6800 --ate 6900 --salvar
    python popular_ficha.py --salvar --headless      # sem abrir janela (mais rápido em lote)
    python popular_ficha.py --salvar --fechar        # fecha o navegador sozinho ao terminar
    python popular_ficha.py --manual                 # abre o url_manual, você loga e abre a ficha, ele preenche
    python popular_ficha.py --manual "https://..."   # idem, com outro link
"""
import argparse
import csv
import datetime as dt
import json
import random
import re
from pathlib import Path
 
from playwright.sync_api import sync_playwright
 
# ============================================================
# CONFIGURAÇÃO
# ============================================================
BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
URL_ORDEM = CONFIG["url"]
ORDEM_PADRAO = CONFIG.get("ordem_padrao", 6832)
ABAS = CONFIG.get("abas", "auto")
ABAS_IGNORAR = [x.upper() for x in CONFIG.get("abas_ignorar", [])]
TEXTOS_SALVAR = CONFIG.get("textos_salvar", ["Salvar"])
CHANCE_NAO_POSSUI = CONFIG.get("chance_nao_possui", 0.0)
MANTER_PREENCHIDOS = CONFIG.get("manter_preenchidos", True)
PREENCHER_CABECALHO = CONFIG.get("preencher_cabecalho", True)
SOBRESCREVER_CABECALHO = CONFIG.get("sobrescrever_cabecalho", False)
URL_MANUAL = CONFIG.get("url_manual", "")
MODO_MANUAL = False   # ligado pelo --manual: o usuário navega e o robô preenche a ficha aberta
PASTA_SESSAO = str(BASE / CONFIG.get("pasta_sessao", "sessao_navegador"))
PASTA_ERROS = str(BASE / "erros")
 
MATERIAIS = ["SAE 1020", "SAE 1045", "SAE 4140", "SAE 4340", "SAE 8620", "ST52", "Inox 304", "Inox 420"]
CROM_BRUN = ["CROMADO", "BRUNIDO", "CROMADO/BRUNIDO"]
DESCRICOES = [
    "Recuperação com cromo duro",
    "Usinagem e polimento",
    "Substituição por peça nova",
    "Retífica e brunimento",
    "Solda e reusinagem",
    "Limpeza e inspeção",
]
 
# ============================================================
# DESCOBERTA DOS CAMPOS (roda dentro da página)
# ============================================================
JS_MAPEAR = r"""
(opts) => {
  opts = opts || {};
  const visivel = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  let R = (opts.raiz && document.querySelector(opts.raiz)) || document;
  // Modo manual: se a ficha abriu numa janela (modal/diálogo), preenche só ela
  if (opts.manual) {
    const dlg = [...document.querySelectorAll('.modal.show, [role=dialog], mat-dialog-container, .p-dialog, .offcanvas.show')]
      .filter(visivel).pop();
    if (dlg) R = dlg;
  }
  // Aba com campos de ficha + formulário "Adicionar": preenche só os campos de ficha
  const soFicha = !!(opts.raiz && R.querySelector('.linha-grupo-ficha-ordem, [class*="valor-campo"]') &&
    [...R.querySelectorAll('button, a.btn, input[type=button]')]
      .some(b => (b.innerText || b.value || '').toUpperCase().includes('ADICIONAR')));
  const limpa = t => (t || '').replace(/\s+/g, ' ').trim();
 
  // Cabeçalho da coluna em tabelas: pega a linha anterior que tenha texto na mesma coluna
  const cabecalhoColuna = el => {
    const td = el.closest('td,th');
    if (!td) return '';
    const tr = td.parentElement;
    const idx = [...tr.children].indexOf(td);
    let prev = tr.previousElementSibling;
    while (prev) {
      const c = prev.children[idx];
      if (c && limpa(c.innerText) && !c.querySelector('input,textarea,select')) return limpa(c.innerText);
      prev = prev.previousElementSibling;
    }
    return '';
  };
 
  const rotulo = el => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && limpa(l.innerText)) return limpa(l.innerText);
    }
    const pai = el.closest('label');
    if (pai && limpa(pai.innerText)) return limpa(pai.innerText);
    if (el.type === 'checkbox' || el.type === 'radio') {
      const prox = el.nextSibling || el.parentElement.nextSibling;
      const t = limpa(el.parentElement.innerText);
      if (t) return t;
    }
    const ant = el.previousElementSibling;
    if (ant && ant.tagName === 'LABEL' && limpa(ant.innerText)) return limpa(ant.innerText);
    return limpa(el.getAttribute('aria-label')) || cabecalhoColuna(el) || cabecalhoAcima(el) ||
           limpa(el.placeholder) || el.name || el.id || '';
  };
 
  // Cabeçalho geométrico: texto curto logo acima do campo, na mesma coluna
  const candidatos = [...R.querySelectorAll('td,th,label,span,div,b,strong,p')]
    .filter(t => !t.querySelector('input,textarea,select') && visivel(t))
    .map(t => ({ t: limpa(t.innerText), r: t.getBoundingClientRect() }))
    .filter(c => c.t.length >= 1 && c.t.length <= 25);
  const cabecalhoAcima = el => {
    const r = el.getBoundingClientRect();
    const cx = (r.left + r.right) / 2;
    let melhor = '', dist = 120;
    for (const c of candidatos) {
      const d = r.top - c.r.bottom;
      if (d >= -2 && d < dist && c.r.left <= cx && cx <= c.r.right) { melhor = c.t; dist = d; }
    }
    return melhor;
  };

  // Contexto: o item/pergunta mais próximo antes do campo (ex.: "Registrar sintomas...")
  const GENERICO = /^(observa|condi[cç][aã]o|itens de verifica|descri[cç][aã]o comercial|medida ideal|n[aã]o possui)/i;
  const textosContexto = [...R.querySelectorAll('label,h3,h4,h5,th,td,b,strong')]
    .filter(t => !t.querySelector('input,textarea,select'))
    .map(t => ({ el: t, t: limpa(t.innerText) }))
    .filter(c => c.t.length >= 3 && c.t.length <= 200 && !GENERICO.test(c.t));
  const contexto = (el, rot) => {
    let melhor = '';
    for (const c of textosContexto) {
      if (c.el.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) {
        if (c.t !== rot) melhor = c.t;
      } else break;
    }
    return melhor;
  };
  // Dicas de máscara/validação que o sistema coloca no campo
  const dica = el => ['class', 'data-mask', 'mask', 'onkeypress', 'onkeyup', 'oninput', 'onblur', 'onchange']
    .map(a => el.getAttribute(a) || '').join(' ').toLowerCase();

  document.querySelectorAll('[data-robo]').forEach(e => e.removeAttribute('data-robo'));
  const campos = [];
  let n = 0;
  R.querySelectorAll('input, textarea, select').forEach(el => {
    const tipo = (el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName.toLowerCase());
    if (['hidden', 'submit', 'button', 'image', 'reset', 'file', 'password'].includes(tipo)) return;
    if (el.disabled || el.readOnly) return;
    if (opts.fora && el.closest('.tab-pane')) return;
    if (el.closest('.slick-slide,.card-product,.zoom-icon,[id^=carousel]')) return;   // legendas/flags das fotos
    if (el.closest('#divDadosOrcamentoOrdem')) return;   // orçamento (descontos, totais, pedidos): não é ficha
    const sobreposto = el.closest('.modal,.dropdown-menu,.bs-searchbox,.bootstrap-datetimepicker-widget,.datepicker,.daterangepicker,.ui-datepicker');
    if (sobreposto && !(R !== document && (sobreposto === R || sobreposto.contains(R)))) return;
    // Modo manual: nunca mexe no menu, na barra de busca nem no topo do sistema
    if (opts.manual && (tipo === 'search' ||
        el.closest('header, nav, aside, [role=navigation], [role=search], #kt_app_header, #kt_app_sidebar, .app-header, .app-sidebar'))) return;
    if (soFicha && !el.closest('.linha-grupo-ficha-ordem') && !/valor-campo/.test(el.className)) return;
    if (!visivel(el) && !['checkbox', 'radio'].includes(tipo)) return;
    const rid = 'r' + (n++);
    el.setAttribute('data-robo', rid);
    const opcoes = el.tagName === 'SELECT'
      ? [...el.options].filter(o => o.value && !o.disabled).map(o => o.value) : [];
    const rot = rotulo(el);
    campos.push({
      rid, tipo, rotulo: rot, secao: -1, protegido: !!opts.fora && (tipo === 'select' || (!el.closest('.linha-grupo-ficha-ordem') && !/valor-campo/.test(el.className))), contexto: contexto(el, rot),
      valor: (tipo === 'checkbox' || tipo === 'radio') ? el.checked : (el.value || ''),
      placeholder: el.placeholder || '', inputmode: el.getAttribute('inputmode') || '',
      pattern: el.getAttribute('pattern') || '', dica: dica(el),
      vmin: el.getAttribute('min'), vmax: el.getAttribute('max'), passo: el.getAttribute('step'),
      nome: el.name || '', grupo: el.name || '', opcoes,
      max: el.maxLength > 0 ? el.maxLength : null,
      y: Math.round(el.getBoundingClientRect().top + window.scrollY),
    });
  });
  return campos;
}
"""
 
# ============================================================
# GERAÇÃO DE VALORES
# ============================================================
POSITIVOS = {"OK", "SIM", "BOM", "APROVADO", "CONFORME", "REUTILIZAR"}

# (palavras do contexto, textos se estiver OK, textos se tiver problema)
REGRAS_OBS = [
    (("SINTOMA", "RUÍDO", "RUIDO"),
     ["Sem ruídos ou sintomas anormais durante a operação."],
     ["Ruído anormal na bomba durante a operação.", "Perda de pressão sob carga.",
      "Lentidão nos movimentos do atuador.", "Aquecimento excessivo do óleo após 15 min de operação."]),
    (("HISTÓRICO", "HISTORICO"),
     ["Última manutenção preventiva há {n} meses, sem ocorrências."],
     ["Sem registro de manutenção nos últimos {n} meses.", "Cliente relata vazamentos recorrentes."]),
    (("TESTE", "PRESSÃO", "PRESSAO", "VAZÃO", "VAZAO", "CORRENTE", "TEMPERATURA"),
     ["Pressão {p} bar, vazão {q} L/min, temperatura {t} °C, corrente {i} A. Dentro do especificado."],
     ["Pressão {p} bar, vazão {q} L/min, temperatura {t} °C, corrente {i} A. Valores fora do especificado."]),
    (("DESGASTE",),
     ["Desgaste interno dentro do normal."], ["Desgaste acentuado, necessária substituição dos componentes."]),
    (("RELATÓRIO", "RELATORIO", "PARÂMETRO", "PARAMETRO"),
     ["Parâmetros registrados no relatório técnico."], ["Parâmetros fora do padrão registrados no relatório técnico."]),
    (("VAZAMENTO", "VEDA"),
     ["Sem vazamentos aparentes."], ["Vazamento identificado nas vedações/conexões."]),
    (("ÓLEO", "OLEO", "FLUIDO", "NÍVEL", "NIVEL"),
     ["Óleo limpo e nível adequado."], ["Óleo contaminado/escurecido, necessária troca."]),
    (("FILTRO",),
     ["Filtro em bom estado."], ["Filtro saturado, necessária substituição."]),
    (("MANGUEIRA", "CONEX", "TUBUL"),
     ["Mangueiras e conexões sem avarias."], ["Mangueira ressecada com início de trinca."]),
    (("MOTOR", "ELÉTRIC", "ELETRIC", "ROLAMENTO"),
     ["Motor elétrico sem anormalidades."], ["Aquecimento e ruído nos rolamentos do motor."]),
    (("FIXA", "PARAFUS", "SUPORTE"),
     ["Fixações firmes e completas."], ["Parafusos de fixação soltos ou faltando."]),
    (("PINTURA", "CORROS", "OXIDA"),
     ["Pintura em bom estado, sem corrosão."], ["Pontos de corrosão na estrutura."]),
]
OBS_OK = ["Peça em bom estado geral.", "Desgaste uniforme, sem ovalização aparente.",
          "Leve risco na região central, dentro da tolerância."]
OBS_NOK = ["Pontos de corrosão superficial, recomenda-se polimento.", "Cromo com descascamento localizado.",
           "Necessário recuperar rosca.", "Marcas de batida próximas à extremidade."]


def observacao(contexto: str, estado):
    """Texto coerente com o item da linha e com a opção marcada (OK / NÃO OK...)."""
    ok = (estado in POSITIVOS) if estado else random.random() < 0.6
    ctx = contexto.upper()
    for chaves, txt_ok, txt_nok in REGRAS_OBS:
        if any(k in ctx for k in chaves):
            return random.choice(txt_ok if ok else txt_nok).format(
                n=random.randint(3, 24), p=random.randint(80, 210), q=random.randint(10, 80),
                t=random.randint(40, 70), i=round(random.uniform(5, 40), 1))
    if contexto and estado and estado != "REUTILIZAR" and estado != "NOVO":
        if len(contexto) > 40:   # item longo: não repete o texto na observação
            return "Verificado, sem anomalias." if ok else "Anomalia identificada, necessário reparo."
        return f"{contexto}: verificado, sem anomalias." if ok else f"{contexto}: anomalia identificada, necessário reparo."
    return random.choice(OBS_OK if ok else OBS_NOK)


def numero(c, a=1, b=100, casas=0):
    """Número dentro do min/max/step do campo, se o campo tiver."""
    try:
        a = float(c["vmin"]) if c.get("vmin") not in (None, "") else a
        b = float(c["vmax"]) if c.get("vmax") not in (None, "") else b
        if c.get("passo") not in (None, "", "any") and float(c["passo"]) >= 1:
            casas = 0
    except ValueError:
        pass
    v = random.uniform(a, max(a, b))
    return str(int(round(v))) if casas == 0 else f"{v:.{casas}f}"


def eh_data(c):
    """Campo de data com datepicker/máscara (classe 'date', 'datepicker'... ou nome txtData...)."""
    if c["tipo"] not in ("text", "date", "datetime-local"):
        return False
    return bool(re.search(r"\b(date|datepicker|datetimepicker)\b", c.get("dica", "")) or
                re.match(r"(txt)?data", (c.get("nome") or "").lower()))


def digitar_data(el, valor):
    """Datepicker com máscara: digita de verdade (fill() não aciona a máscara), fecha o
    calendário e confere; se o campo não aceitou, grava direto e dispara os eventos."""
    el.click()
    el.press("Control+A")
    el.press("Backspace")
    el.press_sequentially(valor, delay=25)
    el.press("Escape")
    el.press("Tab")
    if el.input_value() != valor:
        el.evaluate("""(e, v) => { e.value = v;
            ['input', 'change', 'blur'].forEach(n => e.dispatchEvent(new Event(n, { bubbles: true }))); }""", valor)


VALORES_CABECALHO = {
    "txtnumeroordem": lambda: str(random.randint(3000, 9999)),
    "txtnumeronota": lambda: str(random.randint(1000, 99999)),
    "txtreferenciacliente": lambda: f"REF-{random.randint(1000, 9999)}",
    "txtsolicitante": lambda: random.choice(["Carlos Mendes", "Ana Souza", "João Pereira", "Marina Lima"]),
    "txtnumeroserie": lambda: f"{random.randint(10, 99)}M{random.randint(100000, 999999)}",
    "txtfabricante": lambda: random.choice(["WEG", "Siemens", "SEW Eurodrive", "ABB", "Danfoss", "Schneider"]),
    "txttipoequipamento": lambda: random.choice(["Motor", "Redutor", "Inversor", "Soft-starter"]),
    "txtmodelo": lambda: f"{random.choice('ABCDEF')}{random.randint(100, 999)}-{random.randint(10, 99)}",
    "txtdefeito": lambda: random.choice(["Equipamento não liga", "Ruído e vibração excessivos",
                                         "Aquecimento acima do normal", "Falha intermitente em operação"]),
    "txtanalisepreliminar": lambda: random.choice(["Equipamento recebido, sem avarias externas aparentes.",
                                                   "Indício de desgaste interno, avaliar após desmontagem."]),
    "txtobservacoes": lambda: random.choice(["Equipamento recebido para inspeção e testes.",
                                             "Cliente solicita avaliação completa."]),
}


def valor_campo(c, estado=None):
    """Lê tipo, máscara e rótulo/contexto do campo e gera um valor que faça sentido."""
    tipo, maxlen = c["tipo"], c["max"]
    fixo = VALORES_CABECALHO.get((c.get("nome") or "").lower())
    if fixo:
        v = fixo()
        return v[:maxlen] if maxlen else v
    r = c["rotulo"].upper()
    if not r or re.fullmatch(r"(TXT|CHECK|SELECT|SEL)\d+", r):
        r = "OBS"
    dica = f'{c.get("dica", "")} {c.get("placeholder", "")} {c.get("inputmode", "")}'.lower()
    hoje = dt.date.today() - dt.timedelta(days=random.randint(0, 60))
    if any(k in r for k in ("PREVIST", "ENTREGA", "VENC", "VALID")):   # datas futuras
        hoje = dt.date.today() + dt.timedelta(days=random.randint(1, 20))

    # 1) Pelo tipo/máscara do campo
    if tipo == "date":
        v = hoje.isoformat()
    elif tipo == "datetime-local":
        v = hoje.isoformat() + "T08:00"
    elif tipo == "time":
        v = f"{random.randint(7, 17):02d}:{random.choice(['00', '15', '30', '45'])}"
    elif tipo in ("number", "range"):
        v = numero(c, 1, 100)
    elif tipo == "email":
        v = f"teste{random.randint(1, 999)}@exemplo.com"
    elif tipo == "tel" or "telefone" in dica or "celular" in dica:
        v = f"(11) 9{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"
    elif eh_data(c) or "dd/mm" in dica or "mascaradata" in dica or "DATA" in r:
        v = hoje.strftime("%d/%m/%Y")
    elif any(k in dica for k in ("money", "moeda", "dinheiro", "decimal")):
        v = f"{random.uniform(50, 5000):.2f}".replace(".", ",")
    # 2) Pelo rótulo
    elif "MEDIDA" in r:
        v = f"{random.uniform(10, 150):.2f}"
    elif "DESCRI" in r:
        v = random.choice(DESCRICOES)
    elif tipo == "textarea" or "OBS" in r or "SINTOMA" in r or "REGISTR" in r:
        v = observacao(c.get("contexto", ""), estado)
    elif "MATERIAL" in r:
        v = random.choice(MATERIAIS)
    elif "QTD" in r or "QUANT" in r:
        v = str(random.randint(1, 4))
    elif "CROM" in r or "BRUN" in r:
        v = random.choice(CROM_BRUN)
    elif "COMP" in r:
        v = f"{random.uniform(150, 1500):.1f}"
    elif "EXT" in r:
        v = f"{random.uniform(40, 200):.2f}"
    elif "INT" in r:
        v = f"{random.uniform(25, 160):.2f}"
    elif any(k in r for k in ("DIAM", "Ø", "ROSCA", "ESPESS", "MED", "(MM)", "FOLGA", "ALTURA", "LARG")):
        v = f"{random.uniform(10, 150):.2f}"
    elif any(k in r for k in ("TENS", "VOLT", "(V)")):
        v = str(random.choice([220, 380, 440, 760]))
    elif any(k in r for k in ("CORRENTE", "AMPER", "(A)")):
        v = f"{random.uniform(1, 80):.1f}"
    elif any(k in r for k in ("POT", "KW", "CV", "HP")):
        v = str(random.choice([1, 2, 5, 7.5, 10, 15, 20, 30, 50]))
    elif any(k in r for k in ("RPM", "ROTA")):
        v = str(random.choice([870, 1150, 1750, 3500]))
    elif any(k in r for k in ("PRESS", "BAR", "PSI")):
        v = str(random.randint(50, 350))
    elif any(k in r for k in ("TEMP", "°C")):
        v = str(random.randint(20, 90))
    elif any(k in r for k in ("RESIST", "ISOLA", "OHM", "Ω")):
        v = f"{random.uniform(0.5, 500):.1f}"
    # 3) Campo que só aceita número
    elif "numeric" in dica or "somentenumero" in dica or re.search(r"\\d|\[0-9\]", c.get("pattern", "")):
        v = numero(c, 1, 100)
    else:
        v = observacao(c.get("contexto", ""), estado)
    if maxlen:
        v = v[:maxlen]
    return v


# ============================================================
# PREENCHIMENTO
# ============================================================
def marcar(page, rid, valor: bool):
    """Marca/desmarca via clique em JS: funciona mesmo com o checkbox fora da tela
    ou estilizado (oculto), e dispara o evento que o Angular escuta."""
    page.locator(f'[data-robo="{rid}"]').evaluate(
        "(e, v) => { if (e.checked !== v) e.click(); }", valor)


def nao_possui(c):
    return c["tipo"] == "checkbox" and "NÃO POSSUI" in c["rotulo"].upper()


# Grupos de checkboxes em que só uma opção faz sentido
EXCLUSIVOS = [
    ("REUTILIZAR", "NOVO"),
    ("SIM", "NÃO", "NAO"),
    ("OK", "NOK", "NÃO OK"),
    ("BOM", "REGULAR", "RUIM"),
    ("APROVADO", "REPROVADO"),
    ("CONFORME", "NÃO CONFORME"),
]


def familia_exclusiva(c):
    if c["tipo"] != "checkbox" or nao_possui(c):
        return None
    rot = " ".join(c["rotulo"].upper().rstrip(":").split())
    for i, opcoes in enumerate(EXCLUSIVOS):
        if rot in opcoes or (i == 0 and rot.startswith("REUTILIZ")):
            return i
    return None


FORA = "@fora"   # "aba" virtual: cabeçalho e demais campos que ficam fora das abas


def campos_da_ficha(page, raiz=None):
    """Mapeia os campos da aba (raiz = seletor do painel) e numera a seção
    pela ordem dos checkboxes 'Não possui'."""
    opts = {"raiz": raiz if raiz and raiz.startswith("#") else None, "fora": raiz == FORA, "manual": MODO_MANUAL}
    campos = page.evaluate(JS_MAPEAR, opts)
    secao = -1
    for c in campos:
        if nao_possui(c):
            secao += 1
        c["secao"] = secao
    return campos


def preencher(page, raiz=None, log_campos=None):
    # 1) Decide "Não possui" de cada seção. Seções desmarcadas mostram seus campos.
    secoes_ignoradas = set()
    for c in campos_da_ficha(page, raiz):
        if nao_possui(c):
            ignorar = random.random() < CHANCE_NAO_POSSUI
            marcar(page, c["rid"], ignorar)
            if ignorar:
                secoes_ignoradas.add(c["secao"])
    # 2) Remapeia até a quantidade de campos parar de crescer (a ficha terminou de liberar)
    campos, anterior = [], -1
    for _ in range(10):
        page.wait_for_timeout(500)
        campos = campos_da_ficha(page, raiz)
        if len(campos) == anterior:
            break
        anterior = len(campos)
    if log_campos is not None:
        log_campos.extend(campos)

    # Opções que se excluem (REUTILIZAR/NOVO, SIM/NÃO, OK/NOK...): marca só uma por seção
    pares = {}
    for c in campos:
        fam = familia_exclusiva(c)
        if fam is not None:
            pares.setdefault((c["secao"], fam, round(c["y"] / 20)), []).append(c)

    preenchidos = 0
    escolhas = {}   # (seção, linha) -> opção marcada, para a observação combinar com ela
    for (secao, _, linha), grupo in pares.items():
        if secao in secoes_ignoradas or len(grupo) < 2:
            continue
        ja_marcado = [c for c in grupo if c["valor"] is True]
        if MANTER_PREENCHIDOS and ja_marcado:
            escolhido = ja_marcado[0]
        else:
            escolhido = random.choice(grupo)
            for c in grupo:
                marcar(page, c["rid"], c is escolhido)
        estado = " ".join(escolhido["rotulo"].upper().rstrip(":").split())
        estado = "REUTILIZAR" if estado.startswith("REUTILIZ") else estado
        escolhas[(secao, linha)] = estado
        escolhas.setdefault(("secao", secao), estado)
        preenchidos += 1
    ids_pares = {c["rid"] for g in pares.values() if len(g) >= 2 for c in g}

    radios_feitos = set()
    for c in campos:
        if c["secao"] in secoes_ignoradas or c["rid"] in ids_pares or nao_possui(c):
            continue
        el = page.locator(f'[data-robo="{c["rid"]}"]')
        if c.get("protegido") and not SOBRESCREVER_CABECALHO and (
                c["tipo"] in ("checkbox", "radio") or c["valor"] not in ("", None)):
            preenchidos += 1   # cabeçalho/orçamento: nunca mexe no que já tem dado (nem em marcações)
            continue
        if MANTER_PREENCHIDOS and c["valor"] not in ("", False, None):
            preenchidos += 1   # já tinha valor: mantém o que está no campo
            continue
        try:
            if c["tipo"] == "checkbox":
                marcar(page, c["rid"], random.random() < 0.5)
            elif c["tipo"] == "radio":
                if c["grupo"] in radios_feitos:
                    continue
                grupo = [x for x in campos if x["tipo"] == "radio" and x["grupo"] == c["grupo"]]
                marcar(page, random.choice(grupo)["rid"], True)
                radios_feitos.add(c["grupo"])
            elif c["tipo"] == "select":
                if c["opcoes"]:
                    escolha = random.choice(c["opcoes"])
                    el.select_option(escolha)
                    if c.get("protegido"):
                        print(f"   ! '{c['nome']}' estava vazio: escolhi '{escolha}' (ele define as abas da ficha; "
                              "confira se é o tipo certo)")
            else:
                linha = round(c["y"] / 20)
                estado = next((escolhas[(c["secao"], l)] for l in (linha, linha - 1, linha + 1)
                               if (c["secao"], l) in escolhas), escolhas.get(("secao", c["secao"])))
                valor = valor_campo(c, estado)
                if eh_data(c) or re.fullmatch(r"\d\d/\d\d/\d{4}", valor):
                    digitar_data(el, valor)
                else:
                    el.fill(valor)
                el.dispatch_event("change")
                el.dispatch_event("blur")
            preenchidos += 1
        except Exception as e:
            print(f"   ! não consegui preencher '{c['rotulo']}' (seção {c['secao']}): {type(e).__name__}")
    return len(campos), preenchidos


def abrir_aba(page, aba):
    """Abre a aba (nome, href) e devolve o seletor do painel dela (ex.: '#abaFicha_90').
    Localiza pelo href do painel (único), não pelo texto, e confere se o painel ficou visível."""
    nome, href = aba
    if href:
        link = page.locator(f'a[href="{href}"], [role=tab][href="{href}"], [role=tab][data-target="{href}"]').first
    else:
        link = page.locator("a.nav-link, [role=tab]").filter(has_text=nome).first
    esperar_carregar(page)
    try:
        link.scroll_into_view_if_needed(timeout=5_000)
        link.click(timeout=8_000)
    except Exception:
        link.evaluate("e => e.click()")   # coberta por overlay/fora da tela: clica via JS
    page.wait_for_timeout(1_000)
    esperar_carregar(page)
    if href:
        # painel recém-aberto pode estar vazio (0 de altura) enquanto o sistema renderiza a ficha
        try:
            page.locator(href).first.wait_for(state="visible", timeout=10_000)
        except Exception:
            esperar_carregar(page)
            page.wait_for_timeout(3_000)
            if not page.locator(href).count():
                raise
    return href or None


JS_CLASSIFICAR = r"""
(raiz) => {
  const R = document.querySelector(raiz);
  if (!R) return { ficha: false, motivo: 'painel não encontrado', campos: 0, ficha_n: 0, arquivos: 0, botoes: [] };
  const vis = e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0; };
  const campos = [...R.querySelectorAll('input:not([type=hidden]):not([type=file]):not([type=button]):not([type=submit]), textarea, select')]
    .filter(e => !e.disabled && !e.readOnly && (vis(e) || ['checkbox', 'radio'].includes(e.type)) &&
                 !e.closest('.modal,.dropdown-menu,.bs-searchbox'));
  const fichaN = campos.filter(e => e.closest('.linha-grupo-ficha-ordem') || /valor-campo/.test(e.className)).length;
  const arquivos = R.querySelectorAll('input[type=file]').length;
  const botoes = [...R.querySelectorAll('button, a.btn, input[type=button]')]
    .map(b => (b.innerText || b.value || '').trim()).filter(Boolean);
  const adicionar = botoes.some(t => t.toUpperCase().includes('ADICIONAR'));
  let ficha, motivo;
  if (fichaN > 0) { ficha = true; motivo = adicionar ? 'campos de ficha (o formulário "Adicionar" é ignorado)' : 'campos de ficha'; }
  else if (adicionar) { ficha = false; motivo = 'cadastro (botão Adicionar), sem campos de ficha'; }
  else if (campos.length > 0) { ficha = true; motivo = 'campos de formulário'; }
  else { ficha = false; motivo = arquivos ? 'só envio de arquivos' : 'sem campos'; }
  return { ficha, motivo, campos: campos.length, ficha_n: fichaN, arquivos, botoes: [...new Set(botoes)].slice(0, 6) };
}
"""


def listar_abas(page):
    """Abas da página como [(nome, href)]: todas as visíveis ('auto') ou só as do config
    (o nome do config pode ser parte do nome da aba)."""
    achadas = page.evaluate(r"""() => [...document.querySelectorAll("a.nav-link[href^='#'], [role=tab]")]
        .filter(a => { const r = a.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .map(a => ({ nome: (a.innerText || '').replace(/\s+/g, ' ').trim(),
                     href: a.getAttribute('href') || a.getAttribute('data-target') || '' }))
        .filter(a => a.nome && a.href.startsWith('#') && a.href.length > 1)""")
    abas, vistos = [], set()
    for x in achadas:
        if x["href"] in vistos:
            continue
        vistos.add(x["href"])
        if isinstance(ABAS, list) and not any(n.upper() in x["nome"].upper() for n in ABAS):
            continue
        abas.append((x["nome"], x["href"]))
    return abas


def esperar_abas_estaveis(page, minimo=4, limite=25):
    """Espera a lista de abas parar de mudar. Depois de carregar (ou de mudar o tipo do
    equipamento) a página esconde, alguns segundos depois, as abas dos outros tipos."""
    anterior, estavel = None, 0
    for _ in range(limite):
        atual = [a[1] for a in listar_abas(page)]
        estavel = estavel + 1 if atual == anterior else 0
        if estavel >= minimo:
            return
        anterior = atual
        page.wait_for_timeout(1_000)


def preencher_cabecalho(page, log_campos=None):
    """Cabeçalho e campos fora das abas. Roda ANTES de listar as abas: o tipo do equipamento
    (select fora das abas) decide quais abas existem."""
    esperar_abas_estaveis(page)    # os valores já salvos só aparecem depois de a página assentar
    if not PREENCHER_CABECALHO or not listar_abas(page):
        return 0, 0
    t, f = preencher(page, FORA, log_campos)
    print(f"   - CABEÇALHO / GERAL: {f}/{t} campos")
    esperar_abas_estaveis(page)
    return t, f


def abas_da_ficha(page):
    """Abre cada aba e devolve [(aba, seletor do painel)] só das que são ficha.
    Sem abas na página, devolve a página inteira."""
    esperar_abas_estaveis(page)
    nomes = listar_abas(page)
    if not nomes:
        return [((None, None), None)]
    saida = []
    for aba in nomes:
        nome = aba[0]
        if any(ig in nome.upper() for ig in ABAS_IGNORAR):
            continue
        try:
            raiz = abrir_aba(page, aba)
        except Exception as e:
            print(f"   ! aba '{nome}' não encontrada: {type(e).__name__}")
            continue
        if ABAS == "auto":
            info = page.evaluate(JS_CLASSIFICAR, raiz) if raiz else None
            if not (info and info["ficha"]):
                print(f"   - {nome}: pulada ({info['motivo'] if info else 'sem painel'})")
                continue
        saida.append((aba, raiz))
    return saida


def ativar(page, aba, raiz):
    if raiz and raiz != FORA:
        abrir_aba(page, aba)


def preencher_abas(page, log_campos=None):
    total, feitos = preencher_cabecalho(page, log_campos)
    for aba, raiz in abas_da_ficha(page):
        nome = aba[0] if aba else ""
        ativar(page, aba, raiz)
        t, f = preencher(page, raiz, log_campos)
        print(f"   - {nome or 'página'}: {f}/{t} campos")
        total += t
        feitos += f
    return total, feitos


def auditar(page):
    """Confere o que o robô decide e o que realmente ficou preenchido (não salva nada)."""
    print("\n== 1) CLASSIFICAÇÃO DE TODAS AS ABAS ==")
    esperar_abas_estaveis(page)
    for aba in listar_abas(page):
        try:
            abrir_aba(page, aba)
            i = page.evaluate(JS_CLASSIFICAR, aba[1])
        except Exception as e:
            print(f"  ERRO      {aba[0]}: {type(e).__name__}")
            continue
        print(f"  {'PREENCHER' if i['ficha'] else 'PULAR':9} {aba[0][:34]:34} campos={i['campos']:3} "
              f"ficha={i['ficha_n']:3} arquivos={i['arquivos']:2}  {i['motivo']}  {i['botoes']}")
    print("\n== 2) PREENCHENDO (sem salvar) E RELENDO CADA ABA ==")
    linhas, resumo = [], []
    grupos = []
    if preencher_cabecalho(page) != (0, 0):
        grupos.append((("CABEÇALHO / GERAL", None), FORA, False))
    grupos += [(aba, raiz, True) for aba, raiz in abas_da_ficha(page)]
    for aba, raiz, preencher_agora in grupos:
        nome = aba[0] if aba else "página"
        ativar(page, aba, raiz)
        if preencher_agora:
            preencher(page, raiz)
        campos = campos_da_ficha(page, raiz)
        texto = [c for c in campos if c["tipo"] not in ("checkbox", "radio")]
        vazios = [c for c in texto if c["valor"] in ("", None)]
        caixas = [c for c in campos if c["tipo"] in ("checkbox", "radio")]
        marcadas = [c for c in caixas if c["valor"] is True]
        resumo.append((nome, len(texto) - len(vazios), len(texto), len(marcadas), len(caixas), vazios))
        for c in campos:
            ok = (c["valor"] is True) if c["tipo"] in ("checkbox", "radio") else c["valor"] not in ("", None)
            linhas.append({"aba": nome, "secao": c["secao"], "contexto": c["contexto"], "rotulo": c["rotulo"],
                           "tipo": c["tipo"], "nome": c["nome"], "valor": c["valor"], "ok": "sim" if ok else "NAO"})
    print(f"  {'ABA':36} {'TEXTO/SELECT':>14} {'MARCADOS':>10}  VAZIOS")
    for nome, ok, tot, m, ct, vazios in resumo:
        nomes = ", ".join((c["rotulo"] or c["nome"])[:22] for c in vazios[:5]) + (" ..." if len(vazios) > 5 else "")
        print(f"  {nome[:36]:36} {ok:>6}/{tot:<7} {m:>4}/{ct:<5}  {len(vazios)} {nomes}")
    with open(BASE / "auditoria.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["aba", "secao", "contexto", "rotulo", "tipo", "nome", "valor", "ok"], delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    print(f"\n  Detalhe campo a campo -> {BASE / 'auditoria.csv'}")


def esperar_carregar(page, timeout=60_000):
    """Espera sumir o aviso 'Carregando...' que cobre a tela e bloqueia os cliques."""
    try:
        page.wait_for_function("""() => ![...document.querySelectorAll('body *')].some(e =>
            e.childElementCount === 0 && /carregando/i.test(e.textContent || '') &&
            e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== 'hidden')""",
            timeout=timeout)
    except Exception:
        pass


def abrir(page, url):
    """Abre a página sem depender do evento 'load' (que pode nunca disparar
    em sistemas com SignalR, polling ou recursos lentos)."""
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    # espera aparecer pelo menos um campo da ficha
    # (:visible: o 1º select da página é um selectpicker oculto e travava a espera)
    page.locator("input:not([type=hidden]):visible, textarea:visible").first.wait_for(timeout=30_000)
    esperar_carregar(page)


def esperar_fim(page):
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        page.wait_for_timeout(2_000)
 
 
def clicar_salvar(page):
    for t in TEXTOS_SALVAR:
        btn = page.locator(f"button:text-is('{t}'), input[type=submit][value='{t}'], a:text-is('{t}')")
        for i in range(btn.count()):
            if btn.nth(i).is_visible():
                btn.nth(i).click()
                esperar_fim(page)
                return True
    return False
 
 
def pagina_ativa(ctx):
    """Aba que o usuário está vendo (ele pode ter aberto a ficha numa aba nova)."""
    for pg in reversed(ctx.pages):
        try:
            if pg.evaluate("() => document.visibilityState === 'visible'"):
                return pg
        except Exception:
            pass
    return ctx.pages[-1] if ctx.pages else None


def salvar_campos_csv(campos, nome_arquivo):
    with open(BASE / nome_arquivo, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["aba", "secao", "contexto", "rotulo", "tipo", "valor", "nome", "rid"],
                           extrasaction="ignore", delimiter=";")
        w.writeheader()
        w.writerows(campos)


def modo_manual(ctx, url, salvar_direto):
    """Abre o link, o usuário faz login e abre a ficha; a cada ENTER o robô preenche a ficha da tela."""
    global MODO_MANUAL, MANTER_PREENCHIDOS
    MODO_MANUAL = True
    MANTER_PREENCHIDOS = CONFIG.get("manter_preenchidos_manual", True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if url:
        try:
            page.goto(url, wait_until="commit", timeout=60_000)
        except Exception as e:
            print(f"Aviso: a página não respondeu ({type(e).__name__}). Navegue manualmente na janela aberta.")
    print("\n1) Faça login no navegador (só na 1ª vez: a sessão fica salva).")
    print("2) Abra a ficha que quer preencher.")
    n = 0
    while True:
        resp = input("\n>>> Ficha aberta? ENTER = preencher | M + ENTER = só mapear campos | X + ENTER = sair: ")
        resp = resp.strip().upper()
        if resp == "X":
            break
        page = pagina_ativa(ctx)
        if page is None:
            print("Navegador fechado.")
            break
        page.bring_to_front()
        esperar_carregar(page)
        if resp == "M":
            campos = campos_da_ficha(page)
            salvar_campos_csv(campos, "campos_manual.csv")
            print(f"{len(campos)} campos encontrados em {page.url}\n -> {BASE / 'campos_manual.csv'}")
            continue
        n += 1
        try:
            total, feitos = preencher_abas(page)
            print(f">>> Preenchi {feitos}/{total} campos. Confira no navegador.")
            if total == 0:
                print("   ! Nenhum campo encontrado. Use M + ENTER e me mande o campos_manual.csv.")
                continue
            querer_salvar = salvar_direto or input(
                ">>> S + ENTER = salvar | só ENTER = não salvar (você pode salvar à mão): ").strip().upper() == "S"
            if querer_salvar:
                if clicar_salvar(page):
                    print(">>> Salvo.")
                else:
                    print(f">>> Botão de salvar não encontrado (procurei: {TEXTOS_SALVAR}). Salve à mão.")
        except Exception as e:
            print(f"ERRO {type(e).__name__}: {str(e)[:160]}")
            page.screenshot(path=f"{PASTA_ERROS}/manual_{n}.png", full_page=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", nargs="?", const="", metavar="URL",
                    help="abre o link (ou url_manual do config); você faz login e abre a ficha, o robô preenche")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--mapear", action="store_true", help="só lista os campos")
    ap.add_argument("--auditar", action="store_true", help="confere abas puladas e campos vazios (não salva)")
    ap.add_argument("--salvar", action="store_true", help="clica em Salvar no fim")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--fechar", action="store_true", help="fecha o navegador sozinho ao terminar")
    ap.add_argument("--ordens", nargs="*", type=int)
    ap.add_argument("--de", type=int)
    ap.add_argument("--ate", type=int)
    ap.add_argument("--seed", type=int, help="semente para repetir os mesmos valores")
    a = ap.parse_args()
 
    if a.seed is not None:
        random.seed(a.seed)
    if a.de and a.ate:
        ordens = list(range(a.de, a.ate + 1))
    else:
        ordens = a.ordens or [ORDEM_PADRAO]
 
    Path(PASTA_ERROS).mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PASTA_SESSAO,
            headless=a.headless and not a.login and a.manual is None,
            ignore_https_errors=True,   # certificado de desenvolvimento do localhost
        )
        if a.manual is not None:
            modo_manual(ctx, a.manual or URL_MANUAL, a.salvar)
            ctx.close()
            return

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if a.login:
            try:
                page.goto(URL_ORDEM.format(ordem=ordens[0]), wait_until="commit", timeout=60_000)
            except Exception as e:
                print(f"Aviso: a página não respondeu ({type(e).__name__}). "
                      "Verifique se o sistema está rodando e navegue manualmente na janela aberta.")
            input("Faça login no navegador e aperte ENTER aqui...")
            ctx.close()
            return
 
        if a.mapear:
            abrir(page, URL_ORDEM.format(ordem=ordens[0]))
            campos = []
            esperar_abas_estaveis(page)
            if PREENCHER_CABECALHO and listar_abas(page):
                for c in campos_da_ficha(page, FORA):
                    c["aba"] = "CABEÇALHO / GERAL"
                    campos.append(c)
            for aba, raiz in abas_da_ficha(page):
                ativar(page, aba, raiz)
                for c in campos_da_ficha(page, raiz):
                    c["aba"] = aba[0] if aba else ""
                    campos.append(c)
            with open(BASE / "campos_encontrados.csv", "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=["aba", "secao", "contexto", "rotulo", "tipo", "valor", "nome", "rid"], extrasaction="ignore", delimiter=";")
                w.writeheader()
                w.writerows(campos)
            print(f"{len(campos)} campos encontrados -> campos_encontrados.csv")
            ctx.close()
            return
 
        if a.auditar:
            abrir(page, URL_ORDEM.format(ordem=ordens[0]))
            auditar(page)
            ctx.close()
            return

        for n, oid in enumerate(ordens, 1):
            try:
                abrir(page, URL_ORDEM.format(ordem=oid))
                total, feitos = preencher_abas(page)
                querer_salvar = a.salvar
                if not a.salvar and not a.headless:
                    print(f"\n>>> Ordem {oid} preenchida ({feitos}/{total} campos). Confira no navegador.")
                    resp = input(">>> Digite S + ENTER para salvar, ou só ENTER para NÃO salvar: ")
                    querer_salvar = resp.strip().upper() == "S"
                salvo = clicar_salvar(page) if querer_salvar else False
                status = f"OK {feitos}/{total} campos" + (" (salvo)" if salvo else " (não salvo)")
                if querer_salvar and not salvo:
                    status += " - botão Salvar não encontrado"
            except Exception as e:
                status = f"ERRO {type(e).__name__}: {str(e)[:120]}"
                page.screenshot(path=f"{PASTA_ERROS}/ordem_{oid}.png", full_page=True)
            print(f"[{n}/{len(ordens)}] ordem {oid}: {status}")
 
        if not a.headless and not a.fechar:
            print("\n>>> Terminei. O navegador continua aberto (o que não foi salvo se perde ao fechar).")
            input(">>> Aperte ENTER aqui no terminal quando quiser fechar o navegador...")
        ctx.close()
 

if __name__ == "__main__":
    main()