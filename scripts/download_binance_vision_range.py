from __future__ import annotations
import argparse,csv,io,urllib.request,zipfile
from datetime import date,datetime,timedelta,timezone
from pathlib import Path

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"native-benchmark/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=45) as r: return r.read()
    except Exception as e:
        print("MISS",url,e)
        return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--market",choices=["spot","um"],required=True)
    ap.add_argument("--symbol",required=True)
    ap.add_argument("--timeframe",required=True)
    ap.add_argument("--start",required=True)
    ap.add_argument("--end",required=True)
    ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args()
    s=datetime.strptime(a.start,"%Y-%m-%d").date()
    e=datetime.strptime(a.end,"%Y-%m-%d").date()
    rows=[]
    d=s
    while d<=e:
        ds=d.isoformat()
        fn=f"{a.symbol}-{a.timeframe}-{ds}.zip"
        if a.market=="spot":
            url=f"https://data.binance.vision/data/spot/daily/klines/{a.symbol}/{a.timeframe}/{fn}"
        else:
            url=f"https://data.binance.vision/data/futures/um/daily/klines/{a.symbol}/{a.timeframe}/{fn}"
        b=get(url)
        if b:
            with zipfile.ZipFile(io.BytesIO(b)) as z:
                n=[x for x in z.namelist() if not x.endswith("/")][0]
                txt=io.TextIOWrapper(io.BytesIO(z.read(n)),encoding="utf-8")
                for r in csv.reader(txt):
                    try:t=int(r[0])
                    except:continue
                    if t>100_000_000_000_000:t//=1000
                    ts=datetime.fromtimestamp(t/1000,tz=timezone.utc).isoformat()
                    rows.append((t,ts,r[1],r[2],r[3],r[4],r[5]))
        d+=timedelta(days=1)
    rows=sorted({r[0]:r for r in rows}.values(),key=lambda x:x[0])
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f);w.writerow(["timestamp","open","high","low","close","volume"])
        for _,ts,o,h,l,c,v in rows:w.writerow([ts,o,h,l,c,v])
    print("bars",len(rows),"from",rows[0][1] if rows else None,"to",rows[-1][1] if rows else None)
if __name__=="__main__":main()
