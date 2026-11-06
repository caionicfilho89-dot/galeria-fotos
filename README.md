# 🖼️ Galeria de Fotos

Galeria offline pra Windows (PyQt6): abre uma pasta de fotos com miniaturas rápidas, visualizador com zoom, apresentação com fade, painel de EXIF/GPS e rotação **sem perda** em JPEG.

## Recursos
- Grade de miniaturas geradas em segundo plano, com cache em `%LOCALAPPDATA%\GaleriaFotos\cache` (chave = caminho + data + tamanho).
- Formatos: JPG, PNG, WEBP, BMP, GIF e HEIC (com `pillow-heif`).
- Ordenar por nome, data do arquivo, tamanho ou data EXIF; filtrar pelo nome; pastas recentes; arrastar e soltar.
- Visualizador: zoom (roda do mouse, Ajustar, 100%), arrastar pra mover, ←/→, F11 tela cheia, respeita a orientação EXIF, GIF animado.
- Apresentação com intervalo e fade (Espaço).
- Painel de info: câmera, data, exposição, ISO, lente, dimensões e GPS com link "Abrir no mapa".
- Girar 90°: em JPEG só muda a tag EXIF Orientation (sem recodificar); nos outros formatos pergunta antes de regravar.
- Copiar imagem, mostrar no Explorer, renomear, Lixeira (nunca apaga de vez, sempre confirma), papel de parede (confirma).

## Atalhos no visualizador
←/→ navegar · Espaço apresentação · F ajustar · 1 tamanho real · I info · F11 tela cheia · Ctrl+←/→ girar · F2 renomear · Del Lixeira · Ctrl+C copiar · Esc sair

## Rodar
```
pip install -r requirements.txt
pythonw galeria_fotos.pyw [pasta]
```

## Testes
```
python test_gf.py
```

Licença MIT.
