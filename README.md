# DXF Editor

Editor grafico per file DXF basato su Python, tkinter ed ezdxf.

## Funzionalità

- **Apri/Salva** file DXF (Ctrl+O / Ctrl+S) — il salvataggio crea una copia incrementale (`nome_1.dxf`, `nome_2.dxf`), senza modificare l'originale
- **Visualizzazione** con zoom (rotella mouse) e pan (tasto destro / trackpad)
- **Selezione** - click per selezionare, trascinamento per selezione rettangolare
- **Sposta entità** - trascina le entità selezionate
- **Copia/**Incolla/Taglia (Ctrl+C / Ctrl+V / Ctrl+X)
- **Annulla/Ripeti** (Ctrl+Z / Ctrl+Y)
- **Elimina** (Canc)
- **Allinea a origine** - sposta la selezione in (0,0)
- **Filtro entità** - nascondi entità interne DXF (layer congelati, VIEWPORT, oggetti non grafici)
- **Disegna linee** - bottone "Linea", click due punti per creare una linea
- **Inserisci testo** - bottone "Testo", click punto e inserisci il testo
- **Cambia colore** - seleziona entità, premi "Colore", scegli colore dal picker (palette + codice esadecimale)
- **Colori entità** - lettura corretta dei colori ACI da entità e layer DXF
- **Converti DWG → DXF** tramite ODA File Converter

## Installazione

```bash
pip install -r requirements.txt
```

## Avvio

```bash
python main.py [file.dxf]
```

## Convertitore DWG → DXF

Scarica [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)
e installalo. Al primo utilizzo del bottone **DWG→DXF** ti verrà chiesto di
selezionare il percorso dell'eseguibile. Il percorso viene salvato in
`.oda_config.json`.

## Struttura del progetto

```
editor-dxf/
├── main.py              # Punto di ingresso
├── app/
│   ├── __init__.py
│   ├── constants.py     # Costanti di configurazione
│   ├── colors.py        # Tabella colori ACI
│   ├── geometry.py      # Funzioni geometriche (hit test, spostamento entità)
│   ├── viewport.py      # Viewport e bounding box entità
│   ├── entities.py      # Serializzazione/deserializzazione entità
│   ├── oda.py           # Interfaccia con ODA File Converter
│   └── editor.py        # GUI principale (tkinter)
├── requirements.txt
└── .gitignore
```

## Controlli

| Azione | Mouse | Trackpad |
|--------|-------|----------|
| Seleziona/Sposta | Click sinistro | Click sinistro |
| Pan | Click destro | Scorri |
| Zoom | Rotella | Pizzica |
