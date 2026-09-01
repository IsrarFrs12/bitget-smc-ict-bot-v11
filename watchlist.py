import json, os
from datetime import datetime, timezone
from config import Config

class Watchlist:
    def __init__(self): self.data={}; self._load()
    def _load(self):
        try:
            with open(Config.WATCHLIST_FILE) as f:self.data=json.load(f)
        except (FileNotFoundError,json.JSONDecodeError): self.data={}
    def _save(self):
        os.makedirs(os.path.dirname(Config.WATCHLIST_FILE) or '.',exist_ok=True)
        with open(Config.WATCHLIST_FILE,'w') as f:json.dump(self.data,f,indent=2,default=str)
    def update(self,snapshot):
        s=snapshot['symbol']; item=dict(snapshot); item['updated_at']=datetime.now(timezone.utc).isoformat(); self.data[s]=item; self._save()
    def remove_stale(self,symbols):
        for s in list(self.data):
            if s not in symbols:self.data.pop(s,None)
        self._save()
    def get(self,s):return self.data.get(s)
