"""
modules/demucs.py — Separação de fontes de áudio via Demucs (htdemucs_ft).

Separa o áudio em 4 stems: vocals | drums | bass | other
Combina drums + bass + other → music_and_fx.wav

Fluxo:
  1. Carrega modelo htdemucs_ft na GPU (~3 GB VRAM)
  2. Separa o áudio em stems
  3. Salva vocals.wav e music_fx.wav em audio/<video_id>/
  4. Libera VRAM: del model + torch.cuda.empty_cache()

O vocals.wav vai para o Whisper → transcrição mais limpa.
O music_fx.wav vai para o ShazamIO → identificação de músicas.

VRAM necessária: ~3 GB (NÃO pode coexistir com Whisper large-v3)
Pré-requisito: pip install demucs torch torchaudio
"""

import os
import sys


_cuda_paths_added = False


def _add_cuda_dll_paths() -> None:
    """Mesmo fix de DLLs CUDA do transcript.py — garante que torch acha as DLLs.

    IDEMPOTÊNCIA: flag global garante que só roda uma vez por processo.
    Caso contrário, em batch.py o PATH cresce sem dedup e estoura 32767 chars.
    """
    global _cuda_paths_added
    if _cuda_paths_added or sys.platform != "win32":
        return
    candidates: set[str] = set()
    venv_root = os.path.dirname(os.path.dirname(sys.executable))
    candidates.add(os.path.join(venv_root, "Lib", "site-packages"))
    try:
        import site
        for sp in site.getsitepackages():
            candidates.add(sp)
    except Exception:
        pass
    dll_dirs: list[str] = []
    for sp in candidates:
        nvidia_dir = os.path.join(sp, "nvidia")
        if not os.path.isdir(nvidia_dir):
            continue
        for package in os.listdir(nvidia_dir):
            bin_dir = os.path.join(nvidia_dir, package, "bin")
            if os.path.isdir(bin_dir):
                dll_dirs.append(bin_dir)
                try:
                    os.add_dll_directory(bin_dir)
                except Exception:
                    pass
    if dll_dirs:
        os.environ["PATH"] = ";".join(dll_dirs) + ";" + os.environ.get("PATH", "")
    _cuda_paths_added = True


def separate(audio_path: str, video_id: str, output_dir: str | None = None) -> dict:
    """
    Separa o áudio em componentes (voz / música+fx).

    Args:
        audio_path: caminho do arquivo de áudio original (.mp3/.m4a)
        video_id:   ID do vídeo (para organizar outputs)
        output_dir: diretório base para salvar stems

    Returns:
        {
          success:       bool
          vocals_path:   str | None  — caminho do áudio só com voz
          music_fx_path: str | None  — caminho do áudio sem voz (música + fx)
          stems_dir:     str | None  — diretório com todos os stems
          error:         str | None
          status:        'ok' | 'error' | 'no_demucs'
        }
    """
    result = {
        "success": False,
        "vocals_path": None,
        "music_fx_path": None,
        "stems_dir": None,
        "error": None,
        "status": "error",
    }

    if not audio_path or not os.path.exists(audio_path):
        result["error"] = f"Arquivo de áudio não encontrado: {audio_path}"
        return result

    if output_dir is None:
        from config import AUDIO_PATH
        output_dir = AUDIO_PATH

    # Checa cache — se já separou, retorna paths
    stems_dir = os.path.join(output_dir, f"{video_id}_stems")
    vocals_path = os.path.join(stems_dir, "vocals.wav")
    music_fx_path = os.path.join(stems_dir, "music_fx.wav")

    if os.path.exists(vocals_path) and os.path.exists(music_fx_path):
        result["success"] = True
        result["vocals_path"] = vocals_path
        result["music_fx_path"] = music_fx_path
        result["stems_dir"] = stems_dir
        result["status"] = "ok"
        result["error"] = None
        return result

    # Imports pesados — só carrega se realmente vai usar
    _add_cuda_dll_paths()

    try:
        import torch
        import torchaudio
    except ImportError:
        result["error"] = "torch/torchaudio não instalado. Execute: pip install torch torchaudio"
        result["status"] = "no_demucs"
        return result

    try:
        from demucs.pretrained import get_model
        from demucs.apply import apply_model
    except ImportError:
        result["error"] = "demucs não instalado. Execute: pip install demucs"
        result["status"] = "no_demucs"
        return result

    try:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"   Demucs: carregando htdemucs_ft no {device}...")

        model = get_model("htdemucs_ft")
        model.to(device)
        model.eval()

        # Carrega áudio
        waveform, sr = torchaudio.load(audio_path)

        # Demucs espera sample rate de 44100
        if sr != model.samplerate:
            waveform = torchaudio.functional.resample(waveform, sr, model.samplerate)
            sr = model.samplerate

        # Se mono, duplica para stereo
        if waveform.shape[0] == 1:
            waveform = waveform.repeat(2, 1)

        # apply_model espera (batch, channels, samples)
        ref = waveform.mean(0)
        waveform = (waveform - ref.mean()) / ref.std()
        waveform = waveform.unsqueeze(0).to(device)

        print("   Demucs: separando stems (vocals | drums | bass | other)...")
        with torch.no_grad():
            # shifts=0 é o que torna esta etapa determinística, e precisa ser
            # explícito: o default do apply_model é 1.
            #
            # Com shifts=N o Demucs faz test-time augmentation — desloca a
            # entrada por um offset ALEATÓRIO N vezes e promedia as saídas.
            # Ganha-se um pouco de qualidade na separação e perde-se a
            # reprodutibilidade: o mesmo mp3 produz um vocals.wav diferente a
            # cada execução. Medido com htdemucs_ft sobre o mesmo arquivo —
            # shifts=1 deu dois hashes distintos, shifts=0 deu o mesmo hash.
            #
            # O desvio não para aqui: o vocals.wav alimenta a transcrição, e
            # deslocar a forma de onda move as fronteiras dos segmentos do
            # Whisper. A fronteira é a unidade de todo o resto, então muda o
            # pitch mediano, a posição das emoções e os ratios de ato de fala.
            sources = apply_model(model, waveform, device=device,
                                  shifts=0, progress=True)

        # sources shape: (batch, n_sources, channels, samples)
        # htdemucs_ft sources order: drums, bass, other, vocals
        source_names = model.sources  # ['drums', 'bass', 'other', 'vocals']
        vocals_idx = source_names.index("vocals")

        sources = sources[0]  # remove batch dim
        vocals = sources[vocals_idx].cpu()

        # Combina drums + bass + other → music_fx
        non_vocal_indices = [i for i, name in enumerate(source_names) if name != "vocals"]
        music_fx = sum(sources[i] for i in non_vocal_indices).cpu()

        # Desnormaliza
        vocals = vocals * ref.std() + ref.mean()
        music_fx = music_fx * ref.std() + ref.mean()

        # Salva
        os.makedirs(stems_dir, exist_ok=True)
        torchaudio.save(vocals_path, vocals, sr)
        torchaudio.save(music_fx_path, music_fx, sr)

        # Salva stems individuais também (para análise futura)
        for i, name in enumerate(source_names):
            stem = sources[i].cpu() * ref.std() + ref.mean()
            torchaudio.save(os.path.join(stems_dir, f"{name}.wav"), stem, sr)

        print(f"   Demucs: stems salvos em {stems_dir}/")

        # Libera VRAM
        del model, sources, waveform
        torch.cuda.empty_cache()
        print("   Demucs: VRAM liberada.")

        result["success"] = True
        result["vocals_path"] = vocals_path
        result["music_fx_path"] = music_fx_path
        result["stems_dir"] = stems_dir
        result["status"] = "ok"
        return result

    except Exception as e:
        # Libera VRAM mesmo em caso de erro
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        result["error"] = f"Erro no Demucs: {e}"
        return result
