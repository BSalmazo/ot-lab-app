import argparse
import json
import sys
import subprocess
import platform
from pathlib import Path

DEFAULT_SERVER_URL = "https://web-production-56599.up.railway.app/"
DEFAULT_SESSION_ID = "dev-local-session"
DEFAULT_MODE = "MONITORING"
DEFAULT_IFACE = "ALL"
DEFAULT_PORT_MODE = "MODBUS_PORTS"
DEFAULT_CUSTOM_PORTS = []

CONFIG_DIR = Path.home() / ".ot_lab_agent"
INSTALLED_CONFIG_FILE = CONFIG_DIR / "agent_config.json"


def get_executable_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


LOCAL_BUNDLED_CONFIG = get_executable_dir() / "agent-config.json"


def load_agent_config():
    # Packaged build priority:
    # 1) agent-config.json next to the executable (intended primary source)
    # 2) installed/user config under ~/.ot_lab_agent/agent_config.json
    candidates = [
        LOCAL_BUNDLED_CONFIG,
        INSTALLED_CONFIG_FILE,
    ]

    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    print(f"[agent] config encontrada em: {path}")
                    print(f"[agent] config lida: {data}")
                    return data
            except Exception as e:
                print(f"[agent] falha ao ler config {path}: {e}")

    print("[agent] nenhuma config encontrada")
    return {}


def build_arg_parser(bundled_config):
    parser = argparse.ArgumentParser(description="OT Lab Agent")
    parser.add_argument("--server", default=bundled_config.get("server_url") or DEFAULT_SERVER_URL)
    parser.add_argument("--session-id", default=bundled_config.get("session_id") or DEFAULT_SESSION_ID)
    parser.add_argument("--iface", default=bundled_config.get("iface") or DEFAULT_IFACE)
    parser.add_argument(
        "--port-mode",
        default=bundled_config.get("port_mode") or DEFAULT_PORT_MODE,
        choices=["ALL_PORTS", "MODBUS_PORTS", "CUSTOM"],
    )
    parser.add_argument(
        "--custom-ports",
        default=",".join(str(p) for p in (bundled_config.get("custom_ports") or DEFAULT_CUSTOM_PORTS)),
        help="Comma-separated custom ports (used only when --port-mode CUSTOM)",
    )
    parser.add_argument(
        "--mode",
        default=bundled_config.get("mode") or DEFAULT_MODE,
        choices=["LEARNING", "MONITORING"],
    )
    return parser


def is_npcap_installed():
    """
    Verifica se NPCAP (Windows) ou libpcap (macOS/Linux) está instalado.
    Retorna True se instalado, False caso contrário.
    """
    os_name = platform.system()
    
    try:
        # Tenta importar scapy e verificar se consegue listar interfaces
        from scapy.all import get_if_list
        interfaces = get_if_list()
        
        # Se conseguir listar interfaces, o driver está funcionando
        if interfaces:
            print(f"[agent] ✓ NPCAP/libpcap detectado com sucesso")
            print(f"[agent] Interfaces encontradas: {len(interfaces)}")
            return True
        else:
            print(f"[agent] ⚠ Nenhuma interface detectada - possível problema com NPCAP/libpcap")
            return False
            
    except ImportError as e:
        print(f"[agent] ✗ Scapy não está instalado: {e}")
        return False
    except Exception as e:
        print(f"[agent] ✗ Erro ao verificar NPCAP/libpcap: {e}")
        return False


def ensure_npcap_installed():
    """
    Garante que NPCAP (Windows) ou libpcap (macOS/Linux) está instalado.
    Se não estiver, tenta instalar automaticamente e sai se falhar.
    """
    os_name = platform.system()
    
    print(f"[agent] Verificando NPCAP/libpcap para {os_name}...")
    
    if is_npcap_installed():
        return True
    
    print(f"[agent] ✗ NPCAP/libpcap NÃO está instalado!")
    
    if os_name == "Windows":
        print(f"""
[agent] === AÇÃO REQUERIDA NO WINDOWS ===
[agent] 
[agent] O Npcap é OBRIGATÓRIO para capturar pacotes de rede.
[agent] 
[agent] Por favor, instale o Npcap:
[agent] 1. Baixe em: https://nmap.org/npcap/
[agent] 2. Execute o instalador (execute como administrador)
[agent] 3. Reinicie o bash/terminal
[agent] 4. Execute o agente novamente
[agent] 
[agent] OU use Chocolatey (se instalado):
[agent]    choco install npcap
[agent] 
[agent] ===================================
""")
        sys.exit(1)
        
    elif os_name == "Darwin":  # macOS
        print(f"""
[agent] === AÇÃO REQUERIDA NO MACOS ===
[agent] 
[agent] O libpcap é OBRIGATÓRIO para capturar pacotes de rede.
[agent] 
[agent] Tentando instalar via Homebrew...
""")
        try:
            result = subprocess.run(["brew", "install", "libpcap"], capture_output=True, text=True, timeout=60)
            if result.returncode == 0 or "already installed" in result.stdout:
                print(f"[agent] ✓ libpcap instalado/atualizado com sucesso")
                if is_npcap_installed():
                    return True
            print(f"[agent] ✗ Falha ao instalar libpcap via Homebrew")
        except Exception as e:
            print(f"[agent] ✗ Erro ao chamar Homebrew: {e}")
            
        print(f"""
[agent] 
[agent] Por favor, instale libpcap manualmente:
[agent]   brew install libpcap
[agent] 
[agent] ===================================
""")
        sys.exit(1)
        
    elif os_name == "Linux":
        print(f"""
[agent] === AÇÃO REQUERIDA NO LINUX ===
[agent] 
[agent] O libpcap é OBRIGATÓRIO para capturar pacotes de rede.
[agent] 
[agent] Detectando gerenciador de pacotes...
""")
        
        # Tenta instalar com apt
        if subprocess.run(["which", "apt"], capture_output=True).returncode == 0:
            try:
                print(f"[agent] Tentando instalar via apt...")
                result = subprocess.run(["sudo", "apt", "install", "-y", "libpcap-dev"], 
                                      capture_output=True, text=True, timeout=120)
                if result.returncode == 0:
                    print(f"[agent] ✓ libpcap-dev instalado com sucesso")
                    if is_npcap_installed():
                        return True
            except Exception as e:
                print(f"[agent] ✗ Erro ao instalar via apt: {e}")
        
        # Tenta instalar com yum
        elif subprocess.run(["which", "yum"], capture_output=True).returncode == 0:
            try:
                print(f"[agent] Tentando instalar via yum...")
                result = subprocess.run(["sudo", "yum", "install", "-y", "libpcap-devel"], 
                                      capture_output=True, text=True, timeout=120)
                if result.returncode == 0:
                    print(f"[agent] ✓ libpcap-devel instalado com sucesso")
                    if is_npcap_installed():
                        return True
            except Exception as e:
                print(f"[agent] ✗ Erro ao instalar via yum: {e}")
        
        print(f"""
[agent] 
[agent] Por favor, instale libpcap manualmente:
[agent]   # Debian/Ubuntu:
[agent]   sudo apt install libpcap-dev
[agent]   
[agent]   # RHEL/CentOS/Fedora:
[agent]   sudo yum install libpcap-devel
[agent] 
[agent] ===================================
""")
        sys.exit(1)
    
    else:
        print(f"[agent] ✗ SO não suportado: {os_name}")
        sys.exit(1)
