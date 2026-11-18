import os, io, re, zipfile, tempfile
import pymupdf as fitz
import gradio as gr
from PIL import Image

TEMAS = {
    "Amarelo + Vermelho (Destaque)": ((1.0, 0.98, 0.8), (0.75, 0.0, 0.0), (0.0, 0.0, 0.0)),
    "Cinza + Preto (Engenharia)": ((0.93, 0.94, 0.96), (0.05, 0.05, 0.05), (0.0, 0.0, 0.0)),
    "Azul + Branco (Institucional)": ((0.08, 0.22, 0.48), (1.0, 1.0, 1.0), (0.08, 0.22, 0.48)),
    "Branco + Preto (Simples)": ((1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
}

def ordem_nat(c):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', os.path.basename(c).lower())]

def sincronizar(arqs, fila):
    if not arqs:
        return [], gr.update(value=[], visible=False), gr.update(visible=False), None
    nv = [f if isinstance(f, str) else f.name for f in (arqs if isinstance(arqs, list) else [arqs])]
    mt = [c for c in (fila or []) if c in nv]
    fn = mt + sorted([c for c in nv if c not in mt], key=ordem_nat)
    tb = [[i + 1, os.path.basename(c)] for i, c in enumerate(fn)]
    ok = len(fn) > 1
    return fn, gr.update(value=tb, visible=ok), gr.update(visible=ok), 0

def selecionar_item(evt: gr.SelectData):
    if isinstance(evt.index, (list, tuple)):
        return int(next(iter(evt.index)))
    return int(evt.index)

def mover_item(fila, idx, d):
    if not fila or idx is None:
        return fila, gr.update(), idx
    i, ni = int(idx), int(idx) + d
    if 0 <= i < len(fila) and 0 <= ni < len(fila):
        fila[i], fila[ni] = fila[ni], fila[i]
        i = ni
    return fila, gr.update(value=[[k + 1, os.path.basename(c)] for k, c in enumerate(fila)]), i

def preparar_logo(c_logo):
    if not c_logo or not os.path.exists(c_logo):
        return None, 1.0
    im = Image.open(c_logo)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        im = bg
    else:
        im = im.convert("RGB")
    w, h = im.size
    if h > 160:
        im = im.resize((max(1, int(w * (160.0 / h))), 160), Image.Resampling.LANCZOS)
        w, h = im.size
    b = io.BytesIO()
    im.save(b, format="JPEG", quality=88, optimize=True)
    return b.getvalue(), float(w) / float(max(1, h))

def mostrar_pagina(caminho_pdf, num_pag):
    if not caminho_pdf or not os.path.exists(caminho_pdf):
        return None
    doc = fitz.open(caminho_pdf)
    if len(doc) == 0:
        doc.close()
        return None
    ix = max(0, min(int(num_pag) - 1, len(doc) - 1))
    img_out = os.path.join(os.path.dirname(caminho_pdf), f"pagina_{ix + 1}.png")
    doc.load_page(ix).get_pixmap(dpi=140).save(img_out)
    doc.close()
    return img_out

def carimbar_fitz(doc, pr, n_ini, dig, tema, pos, tf, mx, my, lb, rat):
    num, (bg, fg, bd), tf_f, xref = int(n_ini), TEMAS[tema], float(tf), 0
    for pg in doc:
        if pg.rotation != 0:
            pg.remove_rotation()
        w, h = pg.rect.width, pg.rect.height
        tx = f"{pr}-{str(num).zfill(int(dig))}"
        bw, bh = fitz.get_text_length(tx, fontname="hebo", fontsize=tf_f) + 16.0, tf_f + 8.0
        lw = (bh * rat) if lb else 0.0
        tw = bw + ((lw + 6.0) if lb else 0.0)
        bx = (w - tw - float(mx)) if "Direito" in pos else float(mx)
        by = float(my) if "Superior" in pos else (h - bh - float(my))
        if lb:
            rl = fitz.Rect(bx, by, bx + lw, by + bh)
            xref = pg.insert_image(rl, stream=lb) if xref == 0 else (pg.insert_image(rl, xref=xref) or xref)
            cx = bx + lw + 6.0
        else:
            cx = bx
        pg.draw_rect(fitz.Rect(cx, by, cx + bw, by + bh), color=bd, fill=bg, width=1.2, radius=0.18, overlay=True)
        pg.insert_text(fitz.Point(cx + 8.0, by + bh - 4.5 - (tf_f * 0.05)), tx, fontname="hebo", fontsize=tf_f, color=fg, overlay=True)
        num += 1
    return num

def processar(fila, brutos, logo, pr_txt, m_saida, n_ini, dig, cont, tema, pos, tf, mx, my):
    cams = list(fila) if fila else []
    if not cams and brutos:
        cams = sorted([f if isinstance(f, str) else f.name for f in (brutos if isinstance(brutos, list) else [brutos])], key=ordem_nat)
    if not cams:
        raise gr.Error("Adicione pelo menos um arquivo PDF antes de carimbar.")
    mb_in = sum(os.path.getsize(c) for c in cams) / (1024 * 1024)
    pr = (pr_txt or "P").strip().upper() or "P"
    cl = (logo if isinstance(logo, str) else getattr(logo, "name", None)) if logo else None
    lb, rat = preparar_logo(cl)
    pasta = tempfile.mkdtemp()
    unif = ("Unificar" in m_saida) or ("Ambos" in m_saida)
    sep = ("separados" in m_saida) or ("Ambos" in m_saida) or (len(cams) == 1 and not unif)
    saidas, g_sep, tot_p, pdf_ver = [], [], 0, None

    if unif and len(cams) > 1:
        du = fitz.open()
        for c in cams:
            with fitz.open(c) as di:
                du.insert_pdf(di)
        tot_p = len(du)
        carimbar_fitz(du, pr, n_ini, dig, tema, pos, tf, mx, my, lb, rat)
        cu = os.path.join(pasta, f"{pr}_UNIFICADO_COMPLETO.pdf")
        du.save(cu, garbage=4, deflate=True, deflate_images=True, clean=True)
        du.close()
        saidas.append(cu)
        pdf_ver = cu

    if sep or len(cams) == 1:
        at, tot_p = int(n_ini), 0
        for ix, c in enumerate(cams, 1):
            d = fitz.open(c)
            tot_p += len(d)
            at = carimbar_fitz(d, pr, (at if cont else int(n_ini)), dig, tema, pos, tf, mx, my, lb, rat)
            co = os.path.join(pasta, f"{ix:02d}_{pr}_{os.path.basename(c)}" if len(cams) > 1 else f"{pr}_{os.path.basename(c)}")
            d.save(co, garbage=4, deflate=True, deflate_images=True, clean=True)
            d.close()
            g_sep.append(co)
        if pdf_ver is None and g_sep:
            pdf_ver = next(iter(g_sep))
        if len(cams) == 1 and not ("Ambos" in m_saida):
            saidas = g_sep
        elif g_sep:
            cz = os.path.join(pasta, f"Individuais_{pr}.zip")
            with zipfile.ZipFile(cz, "w", zipfile.ZIP_DEFLATED) as zf:
                for g in g_sep:
                    zf.write(g, arcname=os.path.basename(g))
            saidas.append(cz)

    primeiro_arq = next(iter(saidas))
    mb_out = os.path.getsize(primeiro_arq) / (1024 * 1024)
    msg = f"Concluido! {len(cams)} PDF(s) | {tot_p} pagina(s) carimbadas com '{pr}' | Peso: {mb_in:.1f} MB -> {mb_out:.1f} MB."

    d_ver = fitz.open(pdf_ver)
    qtd_ver = len(d_ver)
    d_ver.close()

    return (
        gr.update(value=saidas, visible=True),
        msg,
        pdf_ver,
        gr.update(minimum=1, maximum=max(2, qtd_ver), value=1, visible=(qtd_ver > 1)),
        gr.update(value=mostrar_pagina(pdf_ver, 1), visible=True),
    )

def limpar_tudo():
    return None, None, [], gr.update(value=[], visible=False), gr.update(visible=False), None, gr.update(value=None, visible=False), "Aguardando envio dos arquivos...", None, gr.update(visible=False, value=1), gr.update(value=None, visible=False)

with gr.Blocks(title="GeoStamp Pro") as demo:
    st_f, st_i, st_v = gr.State([]), gr.State(None), gr.State(None)
    gr.Markdown("## GeoStamp Pro — Carimbador e Unificador de Sondagens")
    with gr.Group():
        pdfs = gr.File(label="1. Adicionar PDFs (Arraste 1 ou varios)", file_count="multiple", file_types=[".pdf"], type="filepath")
        tab = gr.Dataframe(headers=["Ordem", "Arquivo PDF (Clique em uma linha para mover)"], datatype=["number", "str"], interactive=False, visible=False)
        with gr.Row(visible=False) as row_ord:
            b_up, b_dn = gr.Button("Subir Selecionado", size="sm"), gr.Button("Descer Selecionado", size="sm")
    logo = gr.File(label="2. Logomarca Opcional (PNG/JPG - Ex: Cagece)", file_count="single", file_types=["image"], type="filepath")
    with gr.Group():
        with gr.Row():
            at_tipo = gr.Radio(["Percussao (P)", "Trado (T)", "Outro"], value="Percussao (P)", label="3. Atalho de Tipo", scale=6)
            pref = gr.Textbox(label="Prefixo Livre (Editavel)", value="P", max_lines=1, scale=4)
        modo_s = gr.Radio(["Unificar tudo em 1 unico PDF", "Manter separados (ZIP)", "Ambos (Unificado + ZIP)"], value="Unificar tudo em 1 unico PDF", label="4. Formato de Saida")
        cont = gr.Checkbox(label="Numeracao continua entre os arquivos (01, 02, 03...)", value=True)
    with gr.Accordion("Ajustes de Cor, Posicao e Fonte", open=False):
        tema = gr.Dropdown(list(TEMAS.keys()), value="Amarelo + Vermelho (Destaque)", label="Cor do Carimbo")
        pos = gr.Dropdown(["Superior Direito", "Superior Esquerdo", "Inferior Direito", "Inferior Esquerdo"], value="Superior Direito", label="Posicao")
        with gr.Row():
            n_ini = gr.Number(label="N Inicial", value=1, minimum=1, precision=0)
            dig = gr.Slider(label="Digitos", minimum=2, maximum=4, step=1, value=2)
            tf = gr.Slider(label="Fonte", minimum=9, maximum=20, step=1, value=13)
        with gr.Row():
            mx = gr.Slider(label="Margem X", minimum=4, maximum=80, step=1, value=14)
            my = gr.Slider(label="Margem Y", minimum=4, maximum=80, step=1, value=6)
    with gr.Row():
        btn_limpar = gr.Button("Limpar", variant="secondary", scale=3)
        btn_go = gr.Button("CARIMBAR E GERAR ARQUIVOS", variant="primary", size="lg", scale=7)
    status = gr.Markdown("Aguardando envio dos arquivos...")
    saida = gr.File(label="CLIQUE NO ARQUIVO ABAIXO PARA BAIXAR", interactive=False, visible=False)
    barra_pag = gr.Slider(label="Folhear Paginas do PDF Gerado", minimum=1, maximum=2, value=1, step=1, visible=False)
    visor_img = gr.Image(label="Visualizador do Documento Carimbado", type="filepath", interactive=False, visible=False, height=620)

    at_tipo.change(lambda x: "P" if "Percussao" in x else ("T" if "Trado" in x else "SP"), [at_tipo], [pref])
    pdfs.change(sincronizar, [pdfs, st_f], [st_f, tab, row_ord, st_i])
    tab.select(selecionar_item, None, [st_i])

