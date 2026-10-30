# Laplace

Pipeline determinístico e auditável para extração de *fingerprints* culturais
de vídeos curtos brasileiros do YouTube.

Este repositório contém o código que produziu os resultados do artigo
*"Laplace: A Deterministic Pipeline for Emotional Fingerprinting of Brazilian
YouTube Shorts"*. Dado o mesmo `video_id`, o resultado é idêntico entre
execuções, e cada métrica do fingerprint é rastreável até a tabela de origem e
até o modelo ou algoritmo que a produziu.

---

## As 7 etapas

| # | Etapa | Ferramenta | Grava em |
|---|---|---|---|
| 1 | Metadados e comentários | YouTube Data API v3 | `videos`, `metrics`, `comments`, `emoji_stats` |
| 2 | Áudio e separação de fontes | yt-dlp + Demucs `htdemucs_ft` | `audio_files` |
| 3 | Transcrição | faster-whisper `large-v3` | `transcripts`, `transcript_segments`, `timeline_events` |
| 4 | Análise por segmento | regras + BERTimbau/DistilBERT + GoEmotions | `speech_analysis`, `lexical_profile`, `emotion_curve` |
| 5 | Prosódia | librosa | `audio_features` |
| 6 | Sentimento dos comentários | DistilBERT | `comment_sentiments`, `comment_sentiment_agg` |
| 7 | Música e fingerprint | ShazamIO + agregação | `music_events`, `cultural_fingerprint` |

Os três modelos que usam GPU — Demucs (etapa 2), faster-whisper (etapa 3) e
BERTimbau/DistilBERT (etapa 4) — carregam e liberam sequencialmente, com
`torch.cuda.empty_cache()` explícito. Nenhum deles coexiste na memória da placa,
e por isso o pipeline completo cabe em 6 GB de VRAM: os resultados publicados
foram produzidos numa RTX 4050 Laptop de 6 GB.

### Modelos

| Papel | Modelo |
|---|---|
| Separação de fontes | `htdemucs_ft` |
| Transcrição | `faster-whisper large-v3`, idioma fixo em `pt`, `beam_size=5`, VAD com `min_silence_duration_ms=500` |
| Tokenizador | `neuralmind/bert-base-portuguese-cased` |
| Sentimento | `lxyuan/distilbert-base-multilingual-cased-sentiments-student` |
| Emoção | `pysentimiento/bert-pt-emotion` (GoEmotions, 28 categorias) |

---

## Requisitos de máquina

| | |
|---|---|
| Python | 3.11 |
| VRAM | 6 GB bastam — os modelos de GPU carregam e liberam um de cada vez |
| **RAM livre** | **pelo menos 2,5 GB no momento da execução** |
| Disco | ~8 GB para o ambiente + ~4,6 GB de pesos de modelo |
| `ffmpeg` | disponível no `PATH` (exigido pelo yt-dlp e pelo librosa) |
| runtime JS | **Deno ou Node** no `PATH` — ver abaixo |

A RAM é o gargalo real, não a VRAM. Carregar o classificador de sentimento
custa cerca de 1 GB de memória do sistema, e o processo é encerrado pelo
sistema operacional — sem traceback — se não houver folga. Num notebook com
8 GB, um editor pesado aberto já consome a margem necessária.

Para conferir antes de rodar, no PowerShell:

```powershell
$os = Get-CimInstance Win32_OperatingSystem
"{0:N1} GB livres" -f ($os.FreePhysicalMemory/1MB)
```

## Instalação

Requer **Python 3.11**.

```bash
git clone <url-deste-repositorio>
cd laplace

python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux / macOS
```

Instale o torch **e o torchaudio juntos**, antes dos demais pacotes. Para GPU
NVIDIA:

```bash
pip install torch==2.5.1 torchaudio==2.5.1 \
    --index-url https://download.pytorch.org/whl/cu121
```

Sem GPU, omita o `--index-url`. Em seguida:

```bash
pip install -r requirements.txt
```

> As versões de `torch` e `torchaudio` precisam coincidir. O torchaudio é uma
> extensão compilada contra uma versão específica do torch; instalar apenas o
> torch faz o pip resolver o torchaudio mais recente, e o par desencontrado
> falha ao carregar a biblioteca nativa (`WinError 127` no Windows),
> derrubando o Demucs na etapa 2.

O `ffmpeg` precisa estar disponível no `PATH` — é exigido pelo yt-dlp e pelo
librosa.

### Chave da API

O pipeline consulta a YouTube Data API v3, que exige chave própria:

1. Acesse `console.cloud.google.com` e crie um projeto.
2. Ative **YouTube Data API v3** na biblioteca de APIs.
3. Gere uma chave em **Credenciais**.
4. Copie `.env.example` para `.env` e preencha `YOUTUBE_API_KEY`.

A cota padrão é de 10.000 unidades por dia. Cada vídeo consome 1 unidade de
metadados e 1 por página de comentários.

---

## Verificar os resultados do artigo sem executar nada

Os números publicados podem ser conferidos direto de `dados/`, em CSV: são as
medidas dos 100 vídeos do estudo de caso, suficientes para reproduzir as
Tabelas 2, 3 e 4. **Não exigem chave de API, GPU, rede nem download.**

Isso é deliberado. A etapa 1 do pipeline depende de uma plataforma externa que
muda políticas de acesso, limita requisições e pode bloquear downloads
automatizados — nada disso diz respeito ao método, mas tornaria a verificação
refém de circunstância. Os dados derivados tornam os resultados auditáveis de
forma independente; executar o pipeline é necessário apenas para processar
vídeos novos.

Cada arquivo tem uma linha por unidade de medida, e os criadores aparecem como
`C1`–`C10`:

| Arquivo | Linhas | Uma linha por |
|---|---|---|
| `corpus_estudo.csv` | 100 | vídeo do estudo, com o grupo (A ou B) |
| `videos.csv` | 100 | vídeo: canal pseudonimizado, duração, data, idioma |
| `metricas.csv` | 100 | vídeo: views, likes, comentários |
| `cultural_fingerprint.csv` | 100 | vídeo: as 25 colunas do fingerprint |
| `prosodia.csv` | 3.411 | segmento: pitch, volume, energia, ritmo, registro |
| `atos_de_fala.csv` | 3.473 | segmento: ato de fala, sentimento, emoção, evidência |
| `curva_emocional.csv` | 3.473 | segmento: emoção e escore em tempo normalizado |
| `comentarios_agregados.csv` | 100 | vídeo: sentimento agregado da audiência |

As colunas de `cultural_fingerprint.csv` são exatamente as que
[`schema.sql`](schema.sql) declara — `exportar_dados.py` as lê de lá, para que
o CSV publicado e o banco que o pipeline cria não possam divergir.

Os comentários entram **apenas como agregados por vídeo**. O texto individual
não é publicado: um comentário é localizável na plataforma por busca literal, o
que reidentificaria quem o escreveu mesmo com o autor pseudonimizado.

Os scripts de [`analise/`](analise/) leem esses CSVs e imprimem as tabelas do
artigo prontas para conferência.

## Verificar o determinismo

A alegação de determinismo é verificável por script. `validar.py` processa o
mesmo vídeo duas vezes, em bancos separados, e compara as 16 tabelas célula a
célula:

```bash
python validar.py https://www.youtube.com/watch?v=VIDEO_ID
```

Carimbos de tempo e chaves autoincrementais são ignorados na comparação — eles
registram *quando* a execução ocorreu, não o que ela mediu. A saída lista cada
tabela com o número de células comparadas e divergentes.

Medido sobre um vídeo de 37 s: **16 tabelas, 2.770 células, zero divergências.**

O determinismo não é automático, e um ponto do pipeline precisa ser explícito
para que exista. A separação de fontes chama o Demucs com `shifts=0`
(`modules/demucs.py`); o default da biblioteca é `1`, que faz *test-time
augmentation* deslocando a entrada por um offset aleatório. Com o default, o
mesmo MP3 produz um `vocals.wav` diferente a cada execução, e o desvio se
propaga: a forma de onda deslocada move as fronteiras que o Whisper escolhe, e
a fronteira do segmento é a unidade de todas as medidas seguintes.

> O script roda o pipeline **duas vezes**, então leva o dobro do tempo de uma
> execução normal. As duas rodadas são consecutivas de propósito: a etapa 1 lê
> a plataforma ao vivo, e os 100 comentários mais curtidos mudam com o tempo.

## Regerar os dados publicados

```bash
python exportar_dados.py <caminho_do_banco> dados/corpus_estudo.csv
```

Reescreve `dados/*.csv` a partir de um banco, aplicando a pseudonimização. O
segundo argumento define o recorte; sem ele, exporta todos os vídeos do banco.

Nenhum nome de criador aparece no código: o script lê do banco quais canais
existem e deriva deles os tokens a mascarar, inclusive nas colunas de palavras
frequentes — a audiência chama o criador pelo nome, e isso desfaria o
pseudônimo. Um criador novo entra sem nenhuma alteração no script.

## Uso

```bash
python main.py https://www.youtube.com/watch?v=VIDEO_ID
```

Saídas:

- `data/videos.db` — banco SQLite com as 16 tabelas
- `media/exports/laplace.xlsx` — planilha auditável: 19 abas de conteúdo mais 3
  abas separadoras que marcam os blocos estímulo, resposta e síntese
- `media/visualizations/<titulo>__<video_id>.png` — a curva emocional tríplice
- `media/visualizations/<titulo>__<video_id>_timeline.png` — a linha do tempo
  visual, um segmento por linha

Opções: `--no-excel` e `--no-png` pulam as respectivas saídas.

O áudio baixado é reaproveitado entre execuções: rodar duas vezes o mesmo
`video_id` não baixa de novo.

---

## O banco

16 tabelas relacionais, definidas em [`schema.sql`](schema.sql) e criadas
automaticamente na primeira execução. Estão agrupadas em três blocos, que é
também a organização das abas do Excel:

- **Estímulo** — o que o criador emite: `videos`, `transcripts`,
  `transcript_segments`, `speech_analysis`, `audio_features`,
  `timeline_events`, `emotion_curve`, `lexical_profile`, `music_events`,
  `audio_files`
- **Resposta** — como a audiência reage: `metrics`, `comments`,
  `comment_sentiments`, `comment_sentiment_agg`, `emoji_stats`
- **Síntese** — o objeto comparável entre vídeos: `cultural_fingerprint`

Toda classificação inferida grava `classification_evidence` com o que ativou a
decisão (`"bert:positivo(0.87)"`, `"cta_pattern:segue"`), de modo que cada
rótulo pode ser conferido segmento a segmento.

Um `SELECT` em `sqlite_master` devolve 17 nomes, não 16: o SQLite cria
`sqlite_sequence` por conta própria para guardar os contadores das chaves
`AUTOINCREMENT`. Não é tabela do modelo, e `validar.py` não a compara.

---

## Estudo de caso

O estudo de caso do artigo compara 100 vídeos de 10 criadores, divididos entre
avaliações de restaurante (Grupo A) e receitas narradas (Grupo B). A pasta
[`analise/`](analise/) reproduz suas tabelas a partir de `dados/`:

```bash
python analise/tabela2.py    # Tabela 2 — comparação estatística entre grupos
python analise/tabela3.py    # Tabela 3 — gramática emocional de cada nicho
python analise/tabela4.py    # Tabela 4 — reação da audiência, por criador
python analise/dtw.py        # Seção 4 — similaridade narrativa intra-grupo
python analise/planilha.py   # as tabelas acima reunidas num .xlsx, 5 abas
```

`tabela2.py` acrescenta três análises que o manuscrito não traz: a correção
de Benjamini-Hochberg sobre as nove comparações, a sensibilidade a cada
criador e o teste com o criador como unidade de análise. Os três tratam da
pseudo-replicação — ver [`analise/README.md`](analise/README.md).

---

## Limitações conhecidas

Quatro decisões de método não foram calibradas contra anotação humana, e os
resultados que delas dependem devem ser lidos como tais: os limiares do
registro vocal, as regras de ato de fala, o limiar de confiança do sentimento
e o predomínio do rótulo `neutral` na camada de emoção. Todas gravam a
evidência que as ativou, de modo que cada decisão pode ser conferida
segmento a segmento.

O classificador de sentimento é multilíngue, não treinado em português
brasileiro informal, e inverte o sinal em construções nas quais o
intensificador negativo carrega elogio — "um absurdo de bom" é lido como
negativo. A divergência entre essa camada e a de emoção, que usa um modelo
treinado em português, é um indicador útil desse tipo de erro.

O download depende do yt-dlp e está sujeito a bloqueios por parte da
plataforma; falhas de rede nessa etapa não indicam problema no método.

A plataforma passou a proteger parte dos formatos de áudio com desafios em
JavaScript. Sem um runtime JS instalado, o yt-dlp resolve apenas os formatos
desprotegidos, e o download falha com `HTTP Error 403: Forbidden` de maneira
intermitente — depende de qual formato é oferecido na requisição. A etapa 2
detecta **Deno** ou **Node** automaticamente e passa o encontrado ao yt-dlp.
Instalar qualquer um dos dois resolve:

```bash
winget install DenoLand.Deno     # Windows
curl -fsSL https://deno.land/install.sh | sh    # Linux / macOS
```

A identificação musical (etapa 7) consulta um serviço externo e pode expirar
por tempo limite em janelas isoladas. O pipeline registra o aviso e segue com
as janelas que responderam — um vídeo pode, por isso, ter menos faixas
identificadas do que realmente contém.

Sem segmentos de fala não há o que medir: se a transcrição falhar, o pipeline
para e não grava nada, em vez de produzir um fingerprint a partir de dados
parciais.

---

## Dados

Os arquivos `.wav` dos vídeos analisados não são redistribuídos: o conteúdo é
protegido por direitos autorais dos criadores. O que este repositório
disponibiliza são as medidas derivadas — fingerprints, curvas emocionais e
agregados —, que permitem reproduzir as análises sem redistribuir o áudio.

Os comentários coletados contêm identificadores de usuários da plataforma. Os
conjuntos publicados passam por remoção desses identificadores antes da
publicação, em conformidade com a LGPD (Lei nº 13.709/2018), e as análises
usam apenas agregados por vídeo.

Detalhes em [`LICENSE-DADOS.md`](LICENSE-DADOS.md).

## Licença

| | |
|---|---|
| Código | [GNU AGPL-3.0](LICENSE) |
| Dados derivados | [CC BY-NC 4.0](LICENSE-DADOS.md) |

A AGPL exige que qualquer trabalho derivado distribuído — **inclusive
oferecido como serviço pela rede** — publique o código-fonte sob a mesma
licença. O código pode ser lido, executado, verificado e modificado
livremente; o que não pode é ser fechado.
