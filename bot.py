# Compatibility entry point. Run `python bot.py` or `python app.py`.
import asyncio
from app import main

if __name__ == '__main__':
    asyncio.run(main())
