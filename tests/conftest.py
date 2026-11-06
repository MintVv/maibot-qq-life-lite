from pathlib import Path
import sys
import types

root = Path(__file__).resolve().parents[1]
package = types.ModuleType('qq_life_lite')
package.__path__ = [str(root)]
sys.modules['qq_life_lite'] = package

