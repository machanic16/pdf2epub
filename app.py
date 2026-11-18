import os, re, subprocess, sys, tempfile
from flask import Flask, request, send_file, jsonify, after_this_request

app = Flask(__name__, static_folder='static', static_url_path='')
app.config['MAX_CONTENT_LENGTH'] = 300 * 1024 * 1024
SCRIPT = os.path.join(os.path.dirname(__file__), 'pdf2epub.py')

@app.get('/')
def index():
    return app.send_static_file('index.html')

@app.post('/convert')
def convert():
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith('.pdf'):
        return jsonify(error='Please upload a PDF file.'), 400
    base = re.sub(r'[^\w\s.,()\-\'&]+', '_', os.path.splitext(os.path.basename(f.filename))[0]).strip() or 'book'
    work = tempfile.mkdtemp()
    pdf, out = os.path.join(work, base + '.pdf'), os.path.join(work, base + '.epub')
    f.save(pdf)

    @after_this_request
    def cleanup(resp):
        subprocess.run(['rm', '-rf', work])
        return resp


    cmd = [sys.executable, SCRIPT, pdf, '-o', out]
    for key, flag in (('title', '--title'), ('author', '--author'), ('lang', '--lang')):
        v = request.form.get(key, '').strip()
        if v: cmd += [flag, v]
    skip = request.form.get('skip', '').strip()
    if skip:
        if not re.fullmatch(r'[\d\s,\-]+', skip):
            return jsonify(error='Pages to skip must look like "1-4, 651-656".'), 400
        cmd += ['--skip', skip]
    cover = request.files.get('cover')
    if cover and cover.filename:
        ext = os.path.splitext(cover.filename)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png'):
            return jsonify(error='The cover must be a JPEG or PNG image.'), 400
        cover_path = os.path.join(work, 'cover' + ext)
        cover.save(cover_path)
        cmd += ['--cover-image', cover_path]
    elif request.form.get('no_cover') == '1':
        cmd.append('--no-cover')

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=870)
    except subprocess.TimeoutExpired:
        return jsonify(error='Conversion took too long and was stopped.'), 504
    if r.returncode != 0 or not os.path.exists(out):
        msg = (r.stderr or r.stdout).strip().splitlines()
        if msg and 'cover image' in msg[-1]:
            return jsonify(error='The cover image could not be read. Please use a JPEG or PNG.'), 422
        if any('FileDataError' in m for m in msg):
            return jsonify(error="This file isn't a readable PDF."), 422
        return jsonify(error=msg[-1] if msg else 'Conversion failed.'), 422
    resp = send_file(out, mimetype='application/epub+zip', as_attachment=True,
                     download_name=base + '.epub')
    resp.headers['X-Summary'] = ' '.join(r.stdout.splitlines()[1:2]).strip().encode('ascii', 'ignore').decode()
    return resp
