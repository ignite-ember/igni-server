#!/bin/bash
# Modernize Python syntax using pyupgrade
# Converts Optional[X] to X | None, Dict to dict, List to list, etc.

# Install pyupgrade if not already installed
if ! command -v pyupgrade &> /dev/null; then
    echo "Installing pyupgrade..."
    pip install pyupgrade
fi

# Run pyupgrade on all Python files
# --py310-plus: Use Python 3.10+ syntax (| for unions)
# --keep-runtime-typing: Keep typing imports needed at runtime

echo "Modernizing Python syntax in app/ directory..."
find app -name "*.py" -type f -exec pyupgrade --py310-plus {} \;

echo "Done! All Python files have been modernized."
echo ""
echo "Changes made:"
echo "  - Optional[X] → X | None"
echo "  - Union[X, Y] → X | Y"
echo "  - Dict → dict"
echo "  - List → list"
echo "  - Tuple → tuple"
echo "  - Set → set"
