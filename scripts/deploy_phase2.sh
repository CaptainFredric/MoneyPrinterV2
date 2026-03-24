#!/bin/bash
# Deploy Phase 2: Account State Machine
# Safely transition from Phase 1.5 idle mode to Phase 2

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "🚀 Deploying Phase 2: Account State Machine"
echo "==========================================="
echo ""

# Step 1: Stop any running idle mode
echo "[1] Stopping existing idle mode (if running)..."
if [ -f ".mp/runtime/money_idle.pid" ]; then
    OLD_PID=$(cat ".mp/runtime/money_idle.pid" 2>/dev/null || echo "")
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "   Stopping PID $OLD_PID..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
    fi
    rm -f ".mp/runtime/money_idle.pid" ".mp/runtime/money_idle.stop"
fi

# Step 2: Backup old state
echo "[2] Backing up previous state..."
if [ -f ".mp/runtime/money_idle_state.json" ]; then
    cp ".mp/runtime/money_idle_state.json" ".mp/runtime/money_idle_state.json.bak"
    echo "   ✓ Saved backup: money_idle_state.json.bak"
fi

# Step 3: Verify Phase 2 files exist
echo "[3] Verifying Phase 2 files..."
if [ ! -f "src/account_state_machine.py" ]; then
    echo "   ❌ ERROR: account_state_machine.py not found"
    exit 1
fi
if [ ! -f "scripts/money_idle_phase2.py" ]; then
    echo "   ❌ ERROR: money_idle_phase2.py not found"
    exit 1
fi
echo "   ✓ All Phase 2 files present"

# Step 4: Initialize account state machine
echo "[4] Initializing account state machine..."
python3 - <<'INIT_EOF'
from src.account_state_machine import AccountStateMachine
from pathlib import Path

state_file = Path(".mp/runtime/account_states.json")
sm = AccountStateMachine(state_file)

# Initialize managed accounts
accounts = ["niche_launch_1", "EyeCatcher"]
for account in accounts:
    sm.init_account(account)

print(f"   ✓ Initialized {len(accounts)} accounts")
print(f"   Account states: {', '.join(accounts)}")
INIT_EOF

# Step 5: Display initial state
echo "[5] Current account states:"
python3 - <<'STATE_EOF'
import json
from pathlib import Path

state_file = Path(".mp/runtime/account_states.json")
if state_file.exists():
    data = json.loads(state_file.read_text())
    for account, state in data["accounts"].items():
        s = state["state"].upper()
        h = state.get("health_score", 100)
        print(f"   {account:20} → {s:10} (health={h})")
STATE_EOF

# Step 6: Create symlink for easy reference
echo "[6] Creating deployment symlink..."
if [ -L "scripts/money_idle.py" ]; then
    rm "scripts/money_idle.py"
fi
ln -s "money_idle_phase2.py" "scripts/money_idle.py"
echo "   ✓ money_idle.py → money_idle_phase2.py"

echo ""
echo "✅ Phase 2 Deployment Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Test Phase 2: ./venv/bin/python scripts/money_idle_phase2.py --once --headless"
echo "  2. Start daemon: ./venv/bin/python scripts/money_idle_phase2.py --headless &"
echo "  3. View states: cat .mp/runtime/account_states.json"
echo ""
