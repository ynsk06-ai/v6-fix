# BIST AI Proxy - Render Deploy

## 3 Dosya
- main.py       — FastAPI proxy server
- requirements.txt — bağımlılıklar
- render.yaml   — Render konfigürasyonu

## Deploy Adımları

### 1. GitHub'a yükle
```bash
git init
git add .
git commit -m "BIST AI Proxy"
git remote add origin https://github.com/KULLANICI/bist-proxy
git push -u origin main
```

### 2. Render'da deploy
1. render.com → New → Web Service
2. GitHub repoyu bağla
3. Deploy

### 3. URL'i frontend'e ekle
Deploy tamamlanınca URL gelir:
`https://bist-price-proxy-xxxx.onrender.com`

bist_v6.html içinde şu satırı güncelle:
```javascript
var PROXY_URL = 'https://bist-price-proxy-xxxx.onrender.com';
```

## Endpoints

| Endpoint | Açıklama |
|----------|----------|
| GET /health | Sağlık kontrolü |
| GET /prices?symbols=EREGL,TUPRS | Toplu anlık fiyat |
| GET /xu100 | XU100 endeks |
| GET /analyze/EREGL?tf=D | Tek hisse teknik analiz |
| POST /scan | Toplu tarama + sinyal |

## Notlar
- Free tier: 750 saat/ay (yeterli)
- 15 dk işlemsiz kalırsa uyur, ilk istekte ~30 sn uyanır
- Piyasa saatlerinde sürekli aktif tarama uyandırır
