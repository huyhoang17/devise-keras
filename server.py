import time
from flask import render_template, jsonify, request
from multiprocessing import Lock
from keras.models import load_model
from keras.preprocessing.text import text_to_word_sequence
import os, sys
import pickle
import h5py
from rnn_model import hinge_rank_loss
import ipdb
import numpy as np
from flask import Flask
import tensorflow as tf
import random
import argparse
import urllib
import cStringIO
from PIL import Image
import cv2
import sqlite3 

parser = argparse.ArgumentParser(description='server')
parser.add_argument("--word_index", type=str, help="location of the DICT_word_index.VAL/TRAIN.pkl", required=True)
parser.add_argument("--cache", type=str, help="location of the cache.h5 file", required=True)
parser.add_argument("--model", type=str, help="location of the model.hdf5 snapshot", required=True)
parser.add_argument("--threaded", type=int, help="Run flask server in multi--threaded/single--threaded mode", required=True)
parser.add_argument("--host", type=str, help="flask server host in app.run()", required=True)
parser.add_argument("--port", type=int, help="port on which the server will be run", required=True)
parser.add_argument("--dummy", type=int, help="run server in dummy mode for testing js/html/css etc.", required=True)
parser.add_argument("--captions_train", type=str, help="location of string captions of training images", required=True)
parser.add_argument("--captions_valid", type=str, help="location of string captions of validation images", required=True)
parser.add_argument("--vgg16", type=str, help="location of vgg16 weights", required=True)
args = parser.parse_args()

app = Flask(__name__)

DUMMY_MODE = bool(args.dummy)
MODEL_LOC = args.model
WORD_DIM = 300

# VERY IMPORTANT VARIABLES
mutex = Lock()
MAX_SEQUENCE_LENGTH = 20
MODEL=None
DICT_word_index = None

# Load Spacy 
from nlp_stuff import QueryParser
QPObj = QueryParser()

if DUMMY_MODE==False:
	
	MODEL = load_model(MODEL_LOC, custom_objects={"hinge_rank_loss":hinge_rank_loss})
	graph = tf.get_default_graph()
	
	print MODEL.summary()
	
	assert os.path.isfile(args.word_index), "Could not find {}".format(args.word_index)	
	
	with open(args.word_index,"r") as f:
		DICT_word_index = pickle.load(f)
	assert DICT_word_index is not None, "Could not load dictionary that maps word to index"

	im_outs = None 
	fnames = None
	with h5py.File(args.cache) as F:
		im_outs = F["data/im_outs"][:]
		fnames  = F["data/fnames"][:]
	assert im_outs is not None, "Could not load im_outs from cache.h5"
	assert fnames is not None, "Could not load fnames from cache.h5"

	# load the string captions from .json file 
	from pycocotools.coco import COCO
	train_caps = COCO(args.captions_train)
	valid_caps = COCO(args.captions_valid)

	# Load LIME 
	'''
	from simplified_complete_model import FullModel
	model = FullModel(
		rnn_model_path=args.model, 
		word_index_path=args.word_index, 
		vgg_weights_path=args.vgg16
	)
	'''

	
	

def coco_url_to_flickr_url(coco_urls):
	'''
	mscoco.org does no longer host the images. Hence we convert the urls from mscoco.org/images/imgid to its flickr url 
	'''
	flickr_urls = []
	for url in coco_urls:
		imgId = int(url.split("/")[-1])
		fl_url = valid_caps.imgs[imgId]["flickr_url"] # Extract the flickr url from valid_caps (not doing from train_caps yet)
		flickr_urls.append(fl_url)

	assert len(flickr_urls) == len(coco_urls), "flickr_urls is not same length as coco_urls"
	return flickr_urls

def get_string_captions(results_url):
	''' input -> results_url (https://mscoco.org/3456) 
		output -> string_captions corresponding to each result in result_url 
	'''
	results_captions = []
	for result in results_url:
		
		annIds = train_caps.getAnnIds(int(result.split("/")[-1])) # try and find image in train_caps
		if len(annIds) == 0:
			annIds = valid_caps.getAnnIds(int(result.split("/")[-1]))	# if you can't, find it in valid_caps
		assert len(annIds) > 0, "Something wrong here, could not find any caption for given image"
		
		anns = valid_caps.loadAnns(annIds)
		anns = [ c["caption"] for c in anns ]
		results_captions.append(anns)

	return results_captions

# Query string -> word index list 
def query_string_to_word_indices(query_string):
	
	# string -> list of words 
	words = text_to_word_sequence(
			text = query_string,
			filters='!"#$%&()*+,-./:;<=>?@[\\]^_`{|}~\t\n',
			lower=True,
			split=" "
		)

	# check if words in dictionary
	all_words = DICT_word_index.keys()
	for word in words:
		if word not in all_words: 
			_err_msg = "could not find word  | {} | in server's dictionary".format(word)
			raise ValueError(_err_msg)

	# list of words -> list of indices
	words_index = []
	for word in words:
		words_index.append(DICT_word_index[word])

	# pad to 20 words
	if len(words_index) < MAX_SEQUENCE_LENGTH:
		padding = [0 for _ in range(MAX_SEQUENCE_LENGTH - len(words_index))]
		words_index += padding

	if len(words_index) != MAX_SEQUENCE_LENGTH:
		raise ValueError("words_index is not {} numbers long".format(MAX_SEQUENCE_LENGTH))

	return np.array(words_index).reshape((1,MAX_SEQUENCE_LENGTH))

@app.route("/")
@app.route("/index")
def index():
	return render_template("index.html", title="Home")

def run_model(query_string):
	''' This fxn takes a query string
	runs it through the Keras model and returns result.'''

	# run forward pass
	# find diff 
	# get images having closest diff
	print "..waiting to acquire lock"
	result = None
	with mutex:
		print "lock acquired, running model..."
		if DUMMY_MODE:
			
			time.sleep(2)
			
			result = ["static/12345.jpg", "static/32561.jpg", "static/45321.jpg"] 
			
			captions = ["the quick brown fox jumps over the lazy dog."]
			import copy 
			captions = copy.deepcopy(captions) + copy.deepcopy(captions) + copy.deepcopy(captions) + copy.deepcopy(captions) + copy.deepcopy(captions) # each image has 5 captions  
			captions = [ copy.deepcopy(captions) for i in range(3)]                       # we have 3 images, each with 5 captions
			
			assert len(captions) == len(result), " #results != #captions"

			coco_urls = result 
			flickr_urls = result
						
		else:
			assert MODEL is not None, "not in dummy mode, but model did not load!"

			# convert query string to word_index
			try:
				word_indices = query_string_to_word_indices(query_string)
			except Exception, e:
				print str(e)
				return 2, str(e), [], []

			## multithread fix for keras/tf backend
			global graph
			with graph.as_default():
				# forward pass 
				output = MODEL.predict([ np.zeros((1,4096)) , word_indices ])[:, WORD_DIM: ]
				output = output / np.linalg.norm(output, axis=1, keepdims=True)
			
				# compare with im_outs
				TOP_K = 10
				diff = im_outs - output 
				diff = np.linalg.norm(diff, axis=1)
				top_k_indices = np.argsort(diff)[:TOP_K].tolist()

				# populate "results" with fnames of top_k_indices
				result = []
				for k in top_k_indices:
					result.append(fnames[k][0])

				# Replace /var/coco/train2014_clean/COCO_train2014_000000364251.jpg with http://mscoco.org/images/364251
				coco_urls = []
				for r in result:

					imname = r.split("/")[-1] # COCO_train2014_000000364251.jpg
					imname = imname.split("_")[-1] # 000000364251.jpg
					
					i = 0
					while imname[i] == "0":
						i += 1
					imname = imname[i:] # 364251.jpg
					imname = imname.rstrip(".jpg") # 364251
					imname = "http://mscoco.org/images/" + imname # http://mscoco.org/images/364251

					coco_urls.append(imname)
				
				#### NOTE: Since MSCOCO.ORG NO longer hosts images, we need to fetch images from flickr #####
				captions = get_string_captions(coco_urls)	
				flickr_urls = coco_url_to_flickr_url(coco_urls)
				
				
							
		print '..over'
	
	if result is None or len(result)<2:
		return 1,"Err. Model prediction returned None. If you're seeing this, something went horribly wrong at our end.", [], []
	else:
		return 0, flickr_urls, coco_urls, captions

@app.route("/_process_query")
def process_query():

	query_string = request.args.get('query', type=str)
	rc, flickr_urls, coco_urls, captions = run_model(query_string) 
	
	result = {
		"rc":rc,
		"flickr_urls": flickr_urls,
		"coco_urls" : coco_urls,
		"captions": captions
	}

	return jsonify(result)

@app.route("/_get_phrases")
def get_phrases():

	query_string = request.args.get('query', type=str)

	if DUMMY_MODE == True:
		# DUMMY RESULT
		result = {
			"rc" : 0,
			"phrases": ["phrase_one", "phrase_two", "phrase_three"]
		}
	elif DUMMY_MODE == False:
		
		qString = str(query_string)
		cleanString = QPObj.clean_string(qString)
		parse_dict = QPObj.parse_the_string(cleanString)

		noun_chunks = parse_dict["noun_chunks"]
		noun_chunks = map(lambda x: str(x), noun_chunks)

		phrases = []
		node_paths = parse_dict["node_paths"]
		for node_path in node_paths:
			print 'root_node: ', node_path[0]
			for full_node_path in node_path[1]:
				print full_node_path
				phrases.append(" ".join(full_node_path))

		phrases = phrases + noun_chunks
		phrases = map(lambda x: str(x), phrases)

		phrases = [phrase.replace(" ","_") for phrase in phrases]
		

		result = {
			"rc": 0,
			"phrases": phrases
		}

	return jsonify(result)

@app.route("/_get_LIME")
def run_lime():
	''' 
	input -> (phrase, image_id)
	get flickr_url from image_id
	This code does a lookup of the (phrase, flickr_url) tuple.
	return {rc:0, image_name:static/overlays_cache/something.png} 
	'''
	
	phrase = request.args.get('phrase', type=str)
	image_id = request.args.get('image_id', type=str)
	phrase = phrase.strip('"') # '"cooking vegetables"' -> 'cooking vegetables'
	phrase = phrase.replace("_", " ") # 'cat_sitting_on_a_tv' --> 'cat sitting on a tv'
	image_id = image_id.strip('"') # same as above 
	image_id = int(image_id)

	# lookup flickr_url from image_id 
	# ipdb.set_trace()
	assert image_id in valid_caps.imgs.keys(), "This image_id is not available in valid_caps"
	flickr_url = str(valid_caps.imgs[image_id]["flickr_url"])

	conn = sqlite3.connect("lime_results_dbase.db")
	cursor = conn.cursor()

	cursor.execute("select image_name from results WHERE phrase in ('{}') AND flickr_url in ('{}')".format(str(phrase), str(flickr_url)))
	dbase_results = cursor.fetchall()

	## 0th index is phrase
	## 1st index is flickr_url
	## 2nd index is image_name

	if len(dbase_results) == 0:
		print "Err. could not find the pair", flickr_url, image_id, phrase
		# could not find this (flickr_url, phrase) pair in dbase
		result = {
			"rc": 1,
			"lime": "empty"
		}
	elif len(dbase_results) > 1:
		# more than one result for (flickr_url, phrase) pair
		# returning the last result
		print "Found more than one image - returning last", flickr_url, image_id, phrase
		result = {
			"rc": 0,
			"lime": "static/overlays_cache/" + str(dbase_results[-1][0])
		}
	else:
		# everything went fine, one lime image for this (flickr_url, phrase) pair
		result = {
			"rc": 0,
			"lime": "static/overlays_cache/" + str(dbase_results[-1][0])
		}

	return jsonify(result)


if __name__ == '__main__':
	app.run(threaded=bool(args.threaded), host=args.host, port=args.port)
