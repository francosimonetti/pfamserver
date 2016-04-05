from application import app
from database import scoped_db
from flask.ext.restless import APIManager
from models import classes
if classes:
    from models import Uniprot, UniprotRegFull, PfamA, PdbPfamAReg, Pdb, PdbImage
from flask.ext.restful import Api, Resource
import os
from subprocess import Popen as run, PIPE
from StringIO import StringIO
from contextlib import closing
from Bio import AlignIO
from Bio.Align import MultipleSeqAlignment
from itertools import chain
import random
import multiprocessing
from zlib import compress
from base64 import b64encode
from autoupdate.core import Manager


manager = APIManager(app, flask_sqlalchemy_db=scoped_db)

for cls in classes:
    manager.create_api(cls, methods=['GET'])


thread_count = multiprocessing.cpu_count() * 2
print("Working with {:} threads.".format(thread_count))
api = Api(app)
lib_path = app.config['LIB_PATH']
root_path = Manager().actual_version_path()
fetch_call = '{:s}/hmmer/easel/miniapps/esl-afetch'.format(lib_path)
muscle_call = ('{:s}/muscle/src/muscle -maxiters 1 -diags1 -quiet -sv '
               '-distance1 kbit20_3').format(lib_path)
mafft_call = ('MAFFT_BINARIES={0} {0}/mafft --retree 2 --maxiterate 0 '
              '--thread {1} --quiet').format(lib_path + '/mafft/core',
                                             thread_count)


def fill(seqrecord, length):
    seq = seqrecord.seq.__dict__
    seq["_data"] = seq["_data"].ljust(length, '-')
    return seqrecord


def merge(registers):
    pfams = map(lambda reg: StringIO(reg), registers)
    i_msa = map(lambda pfam: AlignIO.read(pfam, "stockholm"), pfams)
    length = max(map(lambda pfam: pfam.get_alignment_length(), i_msa))
    t_msa = map(lambda msa:
                map(lambda sr: fill(sr, length), msa),
                i_msa)
    seqrecords = list(chain(*t_msa))
    msa = MultipleSeqAlignment(seqrecords)
    map(lambda pfam: pfam.close(), pfams)
    return msa


def muscle(msa):
    return run(muscle_call.split(' '),
               stdin=PIPE,
               stdout=PIPE).communicate(input=msa)[0]


def mafft(msa):
    hash = random.getrandbits(128)
    file_in = '{:}.fasta'.format(hash)
    file_out = '{:}_out.fasta'.format(hash)
    with open(file_in, 'w') as f:
        f.write(msa)
    os.system('{:} {:} > {:}'.format(mafft_call, file_in, file_out))
    with open(file_out, 'r') as f:
        msa = f.read()
    os.system('rm {:} {:}'.format(file_in, file_out))
    return msa


algorithms = {
    "muscle": muscle,
    "mafft": mafft
}


def realign(msa, algorithm):
    with closing(StringIO()) as f_tmp:
        count = AlignIO.write(msa, f_tmp, "fasta")
        msa = f_tmp.getvalue()
    msa = algorithms[algorithm](msa)
    with closing(StringIO()) as f_out:
        with closing(StringIO(msa)) as f_in:
            count = AlignIO.convert(f_in, "fasta", f_out, "stockholm")
        msa = f_out.getvalue() if count else ""
    return msa


class StockholmFromPfamAPI(Resource):

    def query(self, query):
        cmd = [fetch_call, root_path + "Pfam-A.full", query]
        return run(cmd, stdout=PIPE).communicate()[0]

    def to_pfam_acc(self, pfam_id):
        quest = scoped_db.query(PfamA).filter(PfamA.pfamA_id == pfam_id).all()
        return quest[0].pfamA_acc if len(quest) else pfam_id

    def get(self, query):
        queries = [query, query.upper(), query.capitalize(), query.lower()]

        for q in queries:
            q_aux = self.to_pfam_acc(q)
            output = self.query(q_aux)
            if output:
                return {'query': q_aux, 'output': b64encode(compress(output))}
        return {'query': query}


class PdbFromSequenceDescriptionAPI(Resource):

    def query(self, uniprot_id, seq_start, seq_end):
        join = (scoped_db.query(Uniprot, UniprotRegFull, PdbPfamAReg, Pdb).
                filter(Uniprot.uniprot_id == uniprot_id).
                filter(UniprotRegFull.seq_start == seq_start).
                filter(UniprotRegFull.seq_end == seq_end).
                filter(UniprotRegFull.uniprot_acc == Uniprot.uniprot_acc).
                filter(UniprotRegFull.auto_uniprot_reg_full ==
                       PdbPfamAReg.auto_uniprot_reg_full).
                filter(PdbPfamAReg.pdb_id == Pdb.pdb_id).
                order_by(PdbPfamAReg.pdb_id).
                order_by(PdbPfamAReg.chain)
                ).all()
        return join

    def serialize(self, element):
        authors = element.Pdb.author.split(',')
        return {
            'pdb_id': element.PdbPfamAReg.pdb_id,
            'chain': element.PdbPfamAReg.chain,
            'pdb_res_start': element.PdbPfamAReg.pdb_res_start,
            'pdb_res_end': element.PdbPfamAReg.pdb_res_end,
            'pfamA_acc': element.UniprotRegFull.pfamA_acc,
            'title': element.Pdb.title,
            'resolution': float(element.Pdb.resolution),
            'method': element.Pdb.method,
            'author': (authors[0] + ' et. al.'
                       if len(authors) > 2 else ''),
            'date': element.Pdb.date
        }

    def get(self, query):
        uniprot_id, seq_start, seq_end = query.split(',')
        response = {
            'query': {
                'uniprot_id': uniprot_id,
                'seq_start': seq_start,
                'seq_end': seq_end}}
        output = self.query(uniprot_id, seq_start, seq_end)
        if output:
            response['output'] = map(self.serialize, output)
        return response


class PdbImageFromPdbAPI(Resource):

    def query(self, query):
        return (scoped_db.query(PdbImage).filter(PdbImage.pdb_id == query.upper())).all()

    def serialize(self, element):
        return {
            'pdb_id': element.pdb_id,
            'pdb_image_sml': b64encode(element.pdb_image_sml)
        }

    def get(self, query):
        output = self.query(query)
        if output:
            return {'query': output[0].pdb_id,
                    'output': map(self.serialize, output)}
        return {'query': query, 'output': output}



class PfamFromUniprotAPI(Resource):

    def query(self, query):
        join = (scoped_db.query(Uniprot, UniprotRegFull, PfamA).
                filter(Uniprot.uniprot_id == query).
                filter(UniprotRegFull.uniprot_acc == Uniprot.uniprot_acc).
                filter(PfamA.pfamA_acc == UniprotRegFull.pfamA_acc).
                order_by(UniprotRegFull.seq_start)).all()
        return join

    def serialize(self, element):
        return {
            'pfamA_acc': element.UniprotRegFull.pfamA_acc,
            'description': element.PfamA.description,
            'seq_start': element.UniprotRegFull.seq_start,
            'seq_end': element.UniprotRegFull.seq_end,
            'num_full': element.PfamA.num_full
        }

    def get(self, query):
        output = self.query(query)
        if output:
            return {'query': output[0].Uniprot.uniprot_id,
                    'output': map(self.serialize, output)}
        return {'query': query, 'output': output}


api.add_resource(StockholmFromPfamAPI,
                 '/api/query/stockholm_pfam/<string:query>',
                 endpoint='stockholm_pfam')
api.add_resource(PfamFromUniprotAPI,
                 '/api/query/pfam_uniprot/<string:query>',
                 endpoint='pfam_uniprot')
api.add_resource(PdbFromSequenceDescriptionAPI,
                 '/api/query/pdb_sequencedescription/<string:query>',
                 endpoint='pdb_sequencedescription')
api.add_resource(PdbImageFromPdbAPI,
                 '/api/query/pdbimage_pdb/<string:query>',
                 endpoint='pdbimage_pdb')
