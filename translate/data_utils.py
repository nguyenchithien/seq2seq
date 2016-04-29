# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Utilities for downloading data from WMT, tokenizing, vocabularies."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import re
import subprocess
import tempfile
import numpy as np
import tensorflow as tf
import math
import logging

import sys

from collections import namedtuple

from tensorflow.python.platform import gfile

# Special vocabulary symbols - we always put them at the start.
_PAD = "_PAD"
_GO = "_GO"
_EOS = "_EOS"
_UNK = "_UNK"
_START_VOCAB = [_PAD, _GO, _EOS, _UNK]

PAD_ID = 0
GO_ID = 1
EOS_ID = 2
UNK_ID = 3

def initialize_vocabulary(vocabulary_path):
  """Initialize vocabulary from file.

  We assume the vocabulary is stored one-item-per-line, so a file:
    dog
    cat
  will result in a vocabulary {"dog": 0, "cat": 1}, and this function will
  also return the reversed-vocabulary ["dog", "cat"].

  Args:
    vocabulary_path: path to the file containing the vocabulary.

  Returns:
    a pair: the vocabulary (a dictionary mapping string to integers), and
    the reversed vocabulary (a list, which reverses the vocabulary mapping).

  Raises:
    ValueError: if the provided vocabulary_path does not exist.
  """
  if gfile.Exists(vocabulary_path):
    rev_vocab = []
    with gfile.GFile(vocabulary_path, mode="r") as f:
      rev_vocab.extend(f.readlines())
    rev_vocab = [line.strip() for line in rev_vocab]
    vocab = dict([(x, y) for (y, x) in enumerate(rev_vocab)])
    return vocab, rev_vocab
  else:
    raise ValueError("Vocabulary file %s not found.", vocabulary_path)


def sentence_to_token_ids(sentence, vocabulary):
  """Convert a string to list of integers representing token-ids.

  For example, a sentence "I have a dog" may become tokenized into
  ["I", "have", "a", "dog"] and with vocabulary {"I": 1, "have": 2,
  "a": 4, "dog": 7"} this function will return [1, 2, 4, 7].

  Args:
    sentence: a string, the sentence to convert to token-ids.
    vocabulary: a dictionary mapping tokens to integers.

  Returns:
    a list of integers, the token-ids for the sentence.
  """
  return [vocabulary.get(w, UNK_ID) for w in sentence.split()]


def get_filenames(data_dir, src_ext, trg_ext, src_vocab_size, trg_vocab_size,
                  train_prefix, dev_prefix, multi_task=False, **kwargs):

  train_path = train_path = os.path.join(data_dir, train_prefix)
  src_train = ["{}.{}".format(train_path, ext) for ext in src_ext]
  src_train_ids = ["{}.ids{}.{}".format(train_path, src_vocab_size, ext) for ext in src_ext]

  if multi_task is not None:  # multi-task setting: one target file for each encoder
    trg_train = ["{}.{}.{}".format(train_path, ext, trg_ext) for ext in src_ext]
    trg_train_ids = ["{}.ids{}.{}.{}".format(train_path, trg_vocab_size, ext, trg_ext) for ext in src_ext]
  else:
    trg_train = "{}.{}".format(train_path, trg_ext)
    trg_train_ids = "{}.ids{}.{}".format(train_path, trg_vocab_size, trg_ext)

  dev_path = os.path.join(data_dir, dev_prefix)
  src_dev = ["{}.{}".format(dev_path, ext) for ext in src_ext]
  trg_dev = "{}.{}".format(dev_path, trg_ext)

  src_dev_ids = ["{}.ids{}.{}".format(dev_path, src_vocab_size, ext) for ext in src_ext]
  trg_dev_ids = "{}.ids{}.{}".format(dev_path, trg_vocab_size, trg_ext)

  src_vocab = [os.path.join(data_dir, "vocab{}.{}".format(src_vocab_size, ext)) for ext in src_ext]
  trg_vocab = os.path.join(data_dir, "vocab{}.{}".format(trg_vocab_size, trg_ext))

  # cleaner than using FLAGS namespace
  files = namedtuple('Files', ['src_train', 'trg_train', 'src_dev', 'trg_dev', 'src_vocab', 'trg_vocab',
                               'src_train_ids', 'trg_train_ids', 'src_dev_ids', 'trg_dev_ids'])

  return files(**{k: v for k, v in locals().items() if k in files._fields})


def bleu_score(bleu_script, hypotheses, references):
  with tempfile.NamedTemporaryFile(delete=False) as f:
    for ref in references:
      f.write(ref + '\n')

  p = subprocess.Popen([bleu_script, f.name], stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, stderr=open('/dev/null', 'w'))

  output, _ = p.communicate('\n'.join(hypotheses))

  m = re.match(r'BLEU = ([^,]*).*BP=([^,]*), ratio=([^,]*)', output)
  values = [float(m.group(i)) for i in range(1, 4)]

  return namedtuple('BLEU', ['score', 'penalty', 'ratio'])(*values)


def read_embeddings(data_dir, src_ext, trg_ext, src_vocab_size, trg_vocab_size,
                    src_vocab, trg_vocab, embedding_prefix, size, **kwargs):
  extensions = src_ext + [trg_ext]
  vocab_paths = src_vocab + [trg_vocab]
  vocab_sizes = [src_vocab_size] * len(src_ext) + [trg_vocab_size]
  embeddings = []

  if not embedding_prefix:
    return None

  for ext, vocab_path, vocab_size in zip(extensions, vocab_paths, vocab_sizes):
    filename = os.path.join(data_dir, "{}.{}".format(embedding_prefix, ext))

    # if embedding file is not given for this language, skip
    if not os.path.isfile(filename):
      embeddings.append(None)
      continue

    with open(filename) as file_:
      lines = (line.split() for line in file_)
      _, size_ = next(lines)
      assert int(size_) == size, 'wrong embedding size'
      embedding = np.zeros((vocab_size, size), dtype="float32")

      d = dict((line[0], np.array(map(float, line[1:]))) for line in lines)

    vocab, _ = initialize_vocabulary(vocab_path)

    for word, index in vocab.iteritems():
      if word in d:
        embedding[index] = d[word]
      else:
        embedding[index] = np.random.uniform(-math.sqrt(3), math.sqrt(3), size)

    embeddings.append(tf.convert_to_tensor(embedding, dtype=tf.float32))

  return embeddings
