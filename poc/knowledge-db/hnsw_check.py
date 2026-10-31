import subprocess, provider
v = provider._vec("port of probeburst")
lit = "[" + ",".join(f"{x:.6f}" for x in v) + "]"
def run(pre):
    sql = f"{pre} SELECT left(content,40), round((embedding <=> '{lit}')::numeric,4) d FROM documents WHERE workspace_name='bl472008' ORDER BY embedding <=> '{lit}' LIMIT 4;"
    out = subprocess.run(["psql","-h","127.0.0.1","-p","5433","-U","postgres","-d","honcho_poc","-At","-c",sql],capture_output=True,text=True)
    return out.stdout.strip() or out.stderr.strip()
print("HNSW default:\n", run(""))
print("exact scan:\n", run("SET enable_indexscan=off; SET enable_bitmapscan=off;"))
print("HNSW ef_search=400:\n", run("SET hnsw.ef_search=400;"))
