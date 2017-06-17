from application import app, cache
from database import scoped_db
from flask.ext.restless import APIManager
from sqlalchemy import or_
from sqlalchemy.orm import Load
from sqlalchemy.orm.exc import NoResultFound
from models import classes
if classes:
    from models import Uniprot, UniprotRegFull, PfamA, PdbPfamAReg, Pdb, PdbImage, \
        PfamARegFullSignificant, Pfamseq
from flask.ext.restful import Api, Resource
from flask_restful.inputs import boolean
from flask import request
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

    def to_pfam_acc(self, code):
        subquery = scoped_db.query(PfamA)
        subquery = subquery.filter(or_(PfamA.pfamA_acc == code.upper(),
                                       PfamA.pfamA_id.ilike(code)))
        subquery = subquery.options(Load(PfamA).load_only("pfamA_acc"))
        try:
            return subquery.one().pfamA_acc
        except NoResultFound as e:
            return None

    @cache.cached(timeout=3600)
    def get(self, query):
        pfamA_acc = self.to_pfam_acc(query)
        output = ''
        if pfamA_acc:
            output = self.query(pfamA_acc)
        return {'query': pfamA_acc,
                'output': b64encode(compress(output))}


class SequenceDescriptionFromPfamAPI(Resource):

    def get_descriptions(self, code, with_pdb):
        #icode = "%{:}%".format(code)
        subquery = scoped_db.query(PfamA)
        subquery = subquery.filter(or_(PfamA.pfamA_acc == code.upper(),
                                       PfamA.pfamA_id.ilike(code))).distinct().subquery()

        #query = scoped_db.query(UniprotRegFull, Uniprot, PdbPfamAReg)
        #query = query.filter(UniprotRegFull.pfamA_acc == subquery.c.pfamA_acc)
        #query = query.filter(UniprotRegFull.auto_uniprot_reg_full == PdbPfamAReg.auto_uniprot_reg_full)
        #query = query.filter(UniprotRegFull.uniprot_acc == Uniprot.uniprot_acc)

        query = scoped_db.query(PfamARegFullSignificant, Pfamseq)
        query = query.join(Pfamseq, Pfamseq.pfamseq_acc == PfamARegFullSignificant.pfamseq_acc)
        query = query.filter(PfamARegFullSignificant.pfamA_acc == subquery.c.pfamA_acc)

        if with_pdb:
            subquery2 = scoped_db.query(PdbPfamAReg)
            subquery2 = subquery2.filter(PdbPfamAReg.pfamA_acc == subquery.c.pfamA_acc).distinct().subquery()
            query = query.filter(PfamARegFullSignificant.pfamseq_acc == subquery2.c.pfamseq_acc)

        query = query.filter(PfamARegFullSignificant.in_full)
        query = query.options(Load(Pfamseq).load_only('pfamseq_id'),
                              Load(PfamARegFullSignificant).load_only("seq_start",
                                                                      "seq_end"))
        query = query.order_by(Pfamseq.pfamseq_id.asc())
        return query.distinct().all()

    def serialize(self, element):
        return "{:}/{:}-{:}".format(
            element.Pfamseq.pfamseq_id,
            element.PfamARegFullSignificant.seq_start,
            element.PfamARegFullSignificant.seq_end)

    @cache.memoize(timeout=3600)
    def get(self, query):
        with_pdb = boolean(request.args.get('with_pdb', 'true'))
        response = {'query': query, 'with_pdb': with_pdb}
        output = self.get_descriptions(query, with_pdb)
        if output:
            response['output'] = map(self.serialize, output)
            response['size'] = len(response['output'])
        return response


class PdbFromSequenceDescriptionAPI(Resource):

    def query(self, uniprot_id, seq_start, seq_end):
        query = scoped_db.query(Uniprot, UniprotRegFull, PdbPfamAReg, Pdb)
        query = query.filter(Uniprot.uniprot_id == uniprot_id,
                             UniprotRegFull.seq_start == seq_start,
                             UniprotRegFull.seq_end == seq_end,
                             UniprotRegFull.uniprot_acc == Uniprot.uniprot_acc,
                             UniprotRegFull.auto_uniprot_reg_full == PdbPfamAReg.auto_uniprot_reg_full,
                             PdbPfamAReg.pdb_id == Pdb.pdb_id)
        query = query.order_by(PdbPfamAReg.pdb_id)
        query = query.order_by(PdbPfamAReg.chain)
        query = query.options(Load(PdbPfamAReg).load_only("pdb_id", "chain", "pdb_res_start", "pdb_res_end"),
                              Load(UniprotRegFull).load_only("pfamA_acc"),
                              Load(Pdb).load_only("title", "resolution", "method", "date", "author"))
        return query.all()

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

    @cache.cached(timeout=3600)
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

    @cache.cached(timeout=3600)
    def get(self, query):
        output = self.query(query)
        if output:
            return {'query': output[0].pdb_id,
                    'output': map(self.serialize, output)}
        return {'query': query, 'output': output}


class PfamFromUniprotAPI(Resource):

    def query(self, query):
        join = (scoped_db.query(Uniprot, UniprotRegFull, PfamA).
                filter(or_(Uniprot.uniprot_id == query,
                           Uniprot.uniprot_acc == query)).
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

    @cache.cached(timeout=3600)
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
api.add_resource(SequenceDescriptionFromPfamAPI,
                 '/api/query/sequencedescription_pfam/<string:query>',
                 endpoint='sequencedescription_pfam')
api.add_resource(PdbFromSequenceDescriptionAPI,
                 '/api/query/pdb_sequencedescription/<string:query>',
                 endpoint='pdb_sequencedescription')
api.add_resource(PdbImageFromPdbAPI,
                 '/api/query/pdbimage_pdb/<string:query>',
                 endpoint='pdbimage_pdb')
