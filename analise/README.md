# Análise do estudo de caso

Reproduz as tabelas do artigo a partir dos CSVs de `dados/`. Não acessa a
rede, não precisa do banco e não executa o pipeline: os quatro scripts leem
apenas arquivos versionados neste repositório.

```bash
python analise/tabela2.py    # comparação estatística entre os grupos
python analise/tabela3.py    # gramática emocional de cada nicho
python analise/tabela4.py    # reação da audiência, por criador
python analise/dtw.py        # similaridade narrativa intra-grupo
python analise/planilha.py   # as tabelas acima reunidas num .xlsx
```

| Script | Tabela do artigo | Lê de |
|---|---|---|
| `tabela2.py` | Tabela 2 | `metricas`, `prosodia`, `curva_emocional`, `cultural_fingerprint` |
| `tabela3.py` | Tabela 3 | `curva_emocional` |
| `tabela4.py` | Tabela 4 | `comentarios_agregados`, `cultural_fingerprint` |
| `dtw.py` | Seção 4, análise narrativa | `curva_emocional` |
| `planilha.py` | todas, em `media/exports/estudo_caso.xlsx` | os quatro scripts acima |

`comum.py` carrega e junta os CSVs; os demais scripts o importam.

A planilha não é versionada, é gerada sob demanda: assim o arquivo binário e
os CSVs não podem contar histórias diferentes. Ela tem cinco abas — as três
tabelas do artigo, o fingerprint por vídeo e as sequências emocionais
codificadas que alimentam o DTW.

## Além do artigo

`tabela2.py` acrescenta três análises que o manuscrito não traz, pedidas na
revisão:

- **Correção de Benjamini-Hochberg** sobre as nove comparações simultâneas.
- **Sensibilidade por criador**: o teste refeito removendo um criador por vez.
- **Criador como unidade de análise**: mediana por criador, cinco contra
  cinco.

Os três endereçam a pseudo-replicação. Os 100 vídeos vêm de 10 criadores, e
tratá-los como 100 observações independentes supõe que vídeos do mesmo canal
não partilham voz, edição nem público. O terceiro bloco é o teste honesto
dessa crítica, e é severo de propósito: com cinco contra cinco, o menor p
possível do Mann-Whitney é 0,0079.

## Identificação dos criadores

Os criadores aparecem como `C1`…`C10`, os mesmos pseudônimos de
`dados/videos.csv`. A correspondência com os canais reais não está neste
repositório.

Vale registrar o limite dessa proteção: os `video_id` são públicos e
necessários à reprodução, de modo que o canal de cada vídeo é recuperável na
plataforma. Os pseudônimos dão neutralidade de apresentação, não anonimato.
Quem é de fato protegido são os comentaristas, que aparecem só em agregados
por vídeo — nunca com texto individual, que seria localizável e
reidentificaria o autor.
