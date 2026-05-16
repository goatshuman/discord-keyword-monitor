import sys
  import os
  sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

  from keep_alive import keep_alive
  from bot import run_bot

  if __name__ == "__main__":
      keep_alive()
      run_bot()
  