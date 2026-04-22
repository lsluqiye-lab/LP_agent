from google.genai import types

try:
    options = types.HttpOptions(timeout=60)
    config = types.GenerateContentConfig(temperature=0.7, http_options=options)
    print("Success")
except Exception as e:
    print(f"Failed: {e}")
