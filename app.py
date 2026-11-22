import streamlit as st
import pandas as pd
import io
import math
import time
import requests
from datetime import datetime
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

st.set_page_config(page_title="Notificaciones UNIJUD — Ruta GPS y PDF", layout="wide", page_icon="📍")

st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    .stButton>button { width: 100%; border-radius: 8px; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

PRESET_ORIGINS = {
    "Juzgado de Garantía de Illapel": (-31.6327, -71.1653),
    "Juzgado de Letras de Salamanca": (-31.7785, -70.9644),
}

def normalize_col(col):
    import unicodedata, re
    s = unicodedata.normalize('NFD', str(col or '')).encode('ascii', 'ignore').decode('utf-8')
    return re.sub(r'[^A-Z0-9]+', '_', s.strip().upper()).strip('_')

def parse_excel_unijud(file_bytes):
    raw_df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str).fillna("")
    header_idx = 0
    for i in range(min(25, len(raw_df))):
        row_norm = [normalize_col(x) for x in raw_df.iloc[i].tolist()]
        hits = sum(1 for k in ["RUC", "RIT", "NOMBRE_PARTICIPANTE", "DOMICILIO", "COMUNA", "ENTE_NOTIFICADOR"] if k in row_norm)
        if hits >= 2:
            header_idx = i
            break

    header_rows = raw_df.iloc[:header_idx].values.tolist()
    raw_headers = [str(x).strip() for x in raw_df.iloc[header_idx].tolist()]
    norm_headers = [normalize_col(x) for x in raw_headers]
    data_df = raw_df.iloc[header_idx + 1:].copy()
    data_df.columns = [f"col_{idx}" for idx in range(len(raw_headers))]

    records = []
    for idx, row in data_df.iterrows():
        raw_dict = {}
        norm_dict = {}
        for c_idx, col_name in enumerate(raw_headers):
            val = str(row.iloc[c_idx]).strip()
            if col_name:
                raw_dict[col_name] = val
            if norm_headers[c_idx]:
                norm_dict[norm_headers[c_idx]] = val

        rit = norm_dict.get("RIT", "")
        ruc = norm_dict.get("RUC", "")
        nombre = norm_dict.get("NOMBRE_PARTICIPANTE") or norm_dict.get("NOMBRE") or norm_dict.get("INTERVINIENTE", "")
        domicilio = norm_dict.get("DOMICILIO") or norm_dict.get("DIRECCION", "")
        if not (rit or ruc or nombre or domicilio):
            continue

        lat_str = norm_dict.get("LATITUD_GPS", "")
        lon_str = norm_dict.get("LONGITUD_GPS", "")
        try:
            lat = float(lat_str) if lat_str else None
            lon = float(lon_str) if lon_str else None
        except ValueError:
            lat, lon = None, None

        records.append({
            "id": norm_dict.get("CRR_NOTIFICACION") or f"{rit or 'REG'}_{idx}",
            "Seleccionar": False,
            "Parada": None,
            "Dist_km": None,
            "RIT": rit,
            "RUC": ruc,
            "VIF": norm_dict.get("VIF", "").upper() == "SI",
            "Tipo_Notif": norm_dict.get("TIPO_NOTIFICACION", ""),
            "Tipo_Part": norm_dict.get("TIPO_PARTICIPANTE", ""),
            "Nombre": nombre,
            "RUT": norm_dict.get("RUT_PARTICIPANTE") or norm_dict.get("RUT", ""),
            "Domicilio": domicilio,
            "Comuna": norm_dict.get("COMUNA") or "Sin Comuna",
            "Ente": norm_dict.get("ENTE_NOTIFICADOR", ""),
            "Fecha_Aud": norm_dict.get("FECHA_AUD", ""),
            "Hora_Aud": norm_dict.get("HORA_AUD", ""),
            "Tipo_Aud": norm_dict.get("TIPO_AUD", ""),
            "Referencia": norm_dict.get("GLS_REFERENCIA") or norm_dict.get("REFERENCIA", ""),
            "Latitud": lat,
            "Longitud": lon,
            "Resultado": norm_dict.get("RESULTADO_TERRENO", ""),
            "Observacion": norm_dict.get("OBSERVACION_TERRENO", ""),
            "Fecha_Gestion": norm_dict.get("FECHA_HORA_GPS", ""),
            "_raw": raw_dict
        })

    return records, header_rows, raw_headers

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def geocode_address(domicilio, comuna):
    q = f"{domicilio}, {comuna}, Chile"
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"format": "json", "limit": 1, "q": q},
            headers={"User-Agent": "JuzgadoGarantiaIllapel-Notificaciones/1.0"},
            timeout=10
        )
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None, None

def build_route_pdf(selected_records, origin_label):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=28)
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7.5, leading=9.5)
    vif_style = ParagraphStyle("vif", parent=cell_style, textColor=colors.HexColor("#b91c1c"))
    head_style = ParagraphStyle("head", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.white, fontName="Helvetica-Bold")

    comunas = ", ".join(sorted(set(r["Comuna"] for r in selected_records)))
    total_km = sum(r["Dist_km"] or 0 for r in selected_records)
    now_str = datetime.now().strftime("%d-%m-%Y %H:%M")

    elements = []
    title_tbl = Table([[
        Paragraph(f"<b>HOJA DE RUTA Y PROYECCIÓN DE DILIGENCIAS EN TERRENO</b><br/><font size=8>Comuna(s): {comunas} | Inicio: {origin_label} | Emisión: {now_str}</font>",
                  ParagraphStyle("hdr", parent=styles["Normal"], fontSize=11, leading=14, textColor=colors.white))
    ]], colWidths=[744])
    title_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1e3a8a")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    elements.append(title_tbl)
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"<b>Total Diligencias Proyectadas:</b> {len(selected_records)}" +
        (f" &nbsp;&nbsp;|&nbsp;&nbsp; <b>Distancia Estimada Acumulada:</b> {total_km:.1f} km" if total_km > 0 else ""),
        ParagraphStyle("meta", parent=styles["Normal"], fontSize=9)
    ))
    elements.append(Spacer(1, 6))

    table_data = [[
        Paragraph("#", head_style),
        Paragraph("RIT / RUC", head_style),
        Paragraph("Interviniente / RUT", head_style),
        Paragraph("Domicilio y Referencia", head_style),
        Paragraph("Notificación / Audiencia", head_style),
        Paragraph("Control en Terreno", head_style),
    ]]

    for idx, r in enumerate(selected_records, 1):
        dist_txt = f"<br/>({r['Dist_km']:.1f} km)" if r["Dist_km"] is not None else ""
        vif_txt = "<br/><b>[CAUSA VIF]</b>" if r["VIF"] else ""
        ref_txt = f"<br/><i>Ref: {r['Referencia']}</i>" if r["Referencia"] else ""
        ctrl_txt = (
            f"<b>{r['Resultado']}</b><br/>{r['Observacion']}"
            if r["Resultado"]
            else "[ ] Notif. Pers.  [ ] Cédula/44<br/>[ ] Búsq. (+)     [ ] Búsq. (-)<br/>Hora: _____  Obs: __________"
        )
        table_data.append([
            Paragraph(f"<b>#{idx}</b>{dist_txt}", cell_style),
            Paragraph(f"<b>{r['RIT'] or 'S/RIT'}</b><br/>RUC: {r['RUC']}{vif_txt}", vif_style if r["VIF"] else cell_style),
            Paragraph(f"<b>{r['Nombre']}</b><br/>RUT: {r['RUT']} ({r['Tipo_Part']})", cell_style),
            Paragraph(f"<b>{r['Domicilio']}</b><br/>Comuna: {r['Comuna']}{ref_txt}", cell_style),
            Paragraph(f"{r['Tipo_Notif']}<br/>Aud: {r['Fecha_Aud']} {r['Hora_Aud']}<br/>{r['Tipo_Aud']}", cell_style),
            Paragraph(ctrl_txt, cell_style),
        ])

    tbl = Table(table_data, colWidths=[42, 92, 132, 195, 130, 153], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(tbl)
    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()

def build_export_excel(records, header_rows, raw_headers):
    extra_cols = ["RESULTADO_TERRENO", "OBSERVACION_TERRENO", "FECHA_HORA_GPS", "LATITUD_GPS", "LONGITUD_GPS", "LINK_GOOGLE_MAPS"]
    base_headers = [h for h in raw_headers if h not in extra_cols]
    final_headers = base_headers + extra_cols

    rows_out = []
    for hr in header_rows:
        padded = hr + [""] * max(0, len(final_headers) - len(hr))
        rows_out.append(padded[:len(final_headers)])
    rows_out.append(final_headers)

    for r in sorted(records, key=lambda x: (x["Comuna"], x["Fecha_Aud"], x["RIT"])):
        base_vals = [r["_raw"].get(h, "") for h in base_headers]
        link = f"https://maps.google.com/?q={r['Latitud']},{r['Longitud']}" if (r["Latitud"] and r["Longitud"]) else ""
        base_vals.extend([
            r["Resultado"],
            r["Observacion"],
            r["Fecha_Gestion"],
            r["Latitud"] if r["Latitud"] is not None else "",
            r["Longitud"] if r["Longitud"] is not None else "",
            link,
        ])
        rows_out.append(base_vals)

    out_buf = io.BytesIO()
    with pd.ExcelWriter(out_buf, engine="openpyxl") as writer:
        pd.DataFrame(rows_out).to_excel(writer, index=False, header=False, sheet_name="Notificaciones_Gestionadas")
    out_buf.seek(0)
    return out_buf.getvalue()

st.title("📍 Diligencias de Notificación UNIJUD — Diseño de Ruta y PDF")

if "records" not in st.session_state:
    st.session_state.records = []
    st.session_state.header_rows = []
    st.session_state.raw_headers = []
    st.session_state.loaded_file = ""

uploaded = st.file_uploader("📂 1. Cargar Planilla Excel UNIJUD (.xlsx / .xls)", type=["xlsx", "xls"])
if uploaded is not None and uploaded.name != st.session_state.loaded_file:
    recs, h_rows, r_headers = parse_excel_unijud(uploaded.read())
    st.session_state.records = recs
    st.session_state.header_rows = h_rows
    st.session_state.raw_headers = r_headers
    st.session_state.loaded_file = uploaded.name
    st.success(f"✅ {len(recs)} diligencias cargadas desde {uploaded.name}")

records = st.session_state.records

if records:
    comunas = ["Todas"] + sorted(set(r["Comuna"] for r in records if r["Comuna"]))
    entes = ["Todos"] + sorted(set(r["Ente"] for r in records if r["Ente"]))

    c1, c2, c3, c4 = st.columns(4)
    f_comuna = c1.selectbox("Comuna", comunas)
    f_ente = c2.selectbox("Ente Notificador", entes)
    f_estado = c3.selectbox("Estado", ["Solo Pendientes", "Solo Seleccionadas (Ruta)", "Todas", "Solo Gestionadas"])
    q_search = c4.text_input("Buscar (RIT, RUC, Nombre, Domicilio)")

    filtered = []
    for r in records:
        if f_comuna != "Todas" and r["Comuna"] != f_comuna:
            continue
        if f_ente != "Todos" and r["Ente"] != f_ente:
            continue
        if f_estado == "Solo Pendientes" and r["Resultado"]:
            continue
        if f_estado == "Solo Seleccionadas (Ruta)" and not r["Seleccionar"]:
            continue
        if f_estado == "Solo Gestionadas" and not r["Resultado"]:
            continue
        if q_search:
            hay = f"{r['RIT']} {r['RUC']} {r['Nombre']} {r['RUT']} {r['Domicilio']} {r['Comuna']}".lower()
            if q_search.lower() not in hay:
                continue
        filtered.append(r)

    filtered.sort(key=lambda x: (not x["Seleccionar"], x["Parada"] if x["Parada"] is not None else 999999, x["Comuna"], x["Fecha_Aud"]))

    st.subheader("🧭 Planificador de Ruta del Día (Respaldo Imprimible)")
    p1, p2, p3, p4 = st.columns([1, 1.5, 2, 1.5])
    top_n = p1.number_input("Cantidad", min_value=1, max_value=100, value=20)
    if p2.button("☑️ Seleccionar Top N"):
        for r in records:
            r["Seleccionar"] = False
            r["Parada"] = None
            r["Dist_km"] = None
        pend = [r for r in filtered if not r["Resultado"]][:int(top_n)]
        for i, r in enumerate(pend, 1):
            r["Seleccionar"] = True
            r["Parada"] = i
        st.rerun()

    origin_choice = p3.selectbox("Punto de Inicio", list(PRESET_ORIGINS.keys()) + ["Primera Parada Marcada"])
    if p4.button("✖ Quitar Selección"):
        for r in records:
            r["Seleccionar"] = False
            r["Parada"] = None
            r["Dist_km"] = None
        st.rerun()

    b1, b2, b3, b4 = st.columns(4)
    if b1.button("📍 1. Ubicar Coordenadas"):
        targets = [r for r in records if r["Seleccionar"] and r["Domicilio"] and (r["Latitud"] is None or r["Longitud"] is None)]
        if not targets:
            st.info("Las diligencias seleccionadas con domicilio ya tienen coordenadas.")
        else:
            prog = st.progress(0)
            for idx, item in enumerate(targets):
                lat, lon = geocode_address(item["Domicilio"], item["Comuna"])
                if lat and lon:
                    item["Latitud"] = lat
                    item["Longitud"] = lon
                prog.progress((idx + 1) / len(targets))
                if idx < len(targets) - 1:
                    time.sleep(1.1)
            st.rerun()

    if b2.button("🧭 2. Ordenar por Cercanía"):
        sel = [r for r in records if r["Seleccionar"]]
        with_c = [r for r in sel if r["Latitud"] is not None and r["Longitud"] is not None]
        without_c = [r for r in sel if r["Latitud"] is None or r["Longitud"] is None]
        if not with_c:
            st.warning("Primero presione '1. Ubicar Coordenadas'.")
        else:
            if origin_choice in PRESET_ORIGINS:
                cur_lat, cur_lon = PRESET_ORIGINS[origin_choice]
            else:
                first = sorted(with_c, key=lambda x: x["Parada"] or 999)[0]
                cur_lat, cur_lon = first["Latitud"], first["Longitud"]

            unvisited = list(with_c)
            step = 1
            while unvisited:
                best_i = min(range(len(unvisited)), key=lambda i: haversine_km(cur_lat, cur_lon, unvisited[i]["Latitud"], unvisited[i]["Longitud"]))
                chosen = unvisited.pop(best_i)
                chosen["Dist_km"] = haversine_km(cur_lat, cur_lon, chosen["Latitud"], chosen["Longitud"])
                chosen["Parada"] = step
                step += 1
                cur_lat, cur_lon = chosen["Latitud"], chosen["Longitud"]
            for r in without_c:
                r["Parada"] = step
                r["Dist_km"] = None
                step += 1
            st.rerun()

    selected_route = sorted([r for r in records if r["Seleccionar"]], key=lambda x: x["Parada"] or 9999)
    if selected_route:
        pdf_bytes = build_route_pdf(selected_route, origin_choice)
        b3.download_button(
            "📄 3. Descargar Ruta en PDF",
            data=pdf_bytes,
            file_name=f"Ruta_Diligencias_{datetime.now().strftime('%Y-%m-%d')}.pdf",
            mime="application/pdf",
        )
    else:
        b3.button("📄 3. Descargar Ruta en PDF", disabled=True)

    xlsx_bytes = build_export_excel(records, st.session_state.header_rows, st.session_state.raw_headers)
    b4.download_button(
        "📥 Exportar Excel (.xlsx)",
        data=xlsx_bytes,
        file_name=f"Notificaciones_Terreno_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.divider()
    st.caption(f"Mostrando {len(filtered)} de {len(records)} registros | Seleccionadas para ruta: {len(selected_route)}")

    df_view = pd.DataFrame([{
        "id": r["id"],
        "Ruta": r["Seleccionar"],
        "Parada": r["Parada"],
        "Dist_km": round(r["Dist_km"], 1) if r["Dist_km"] is not None else None,
        "RIT": r["RIT"],
        "VIF": "SÍ" if r["VIF"] else "",
        "Interviniente": r["Nombre"],
        "Domicilio": r["Domicilio"],
        "Comuna": r["Comuna"],
        "Audiencia": f"{r['Fecha_Aud']} {r['Hora_Aud']}".strip(),
        "Resultado": r["Resultado"],
        "Observacion": r["Observacion"],
    } for r in filtered])

    edited = st.data_editor(
        df_view,
        hide_index=True,
        use_container_width=True,
        column_config={
            "id": None,
            "Ruta": st.column_config.CheckboxColumn("En Ruta"),
            "Parada": st.column_config.NumberColumn("Parada #", disabled=True),
            "Dist_km": st.column_config.NumberColumn("Dist. (km)", disabled=True),
            "Resultado": st.column_config.SelectboxColumn(
                "Resultado Terreno",
                options=[
                    "",
                    "Notificado personalmente",
                    "Notificado por cédula / Art. 44 CPC",
                    "Búsqueda positiva (domicilio confirmado, no habido)",
                    "Búsqueda negativa (no reside / desconocido en el lugar)",
                    "Domicilio inexistente / numeración no ubicada",
                    "Sin moradores / inmueble cerrado",
                ],
            ),
        },
        disabled=["Parada", "Dist_km", "RIT", "VIF", "Interviniente", "Domicilio", "Comuna", "Audiencia"],
    )

    rec_map = {r["id"]: r for r in records}
    changed = False
    for _, row in edited.iterrows():
        rec = rec_map.get(row["id"])
        if rec:
            if rec["Seleccionar"] != bool(row["Ruta"]):
                rec["Seleccionar"] = bool(row["Ruta"])
                if not rec["Seleccionar"]:
                    rec["Parada"] = None
                    rec["Dist_km"] = None
                changed = True
            if rec["Resultado"] != (row["Resultado"] or ""):
                rec["Resultado"] = row["Resultado"] or ""
                rec["Fecha_Gestion"] = datetime.now().strftime("%d-%m-%Y %H:%M") if rec["Resultado"] else ""
                changed = True
            if rec["Observacion"] != (row["Observacion"] or ""):
                rec["Observacion"] = row["Observacion"] or ""
                changed = True
    if changed:
        st.rerun()
