#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Author: Rico Sennrich
# flake8: noqa

"""Use byte pair encoding (BPE) to learn a variable-length encoding of the vocabulary in a text.
Unlike the original BPE, it does not compress the plain text, but can be used to reduce the vocabulary
of a text to a configurable number of symbols, with only a small increase in the number of tokens.

Reference:
Rico Sennrich, Barry Haddow and Alexandra Birch (2016). Neural Machine Translation of Rare Words with Subword Units.
Proceedings of the 54th Annual Meeting of the Association for Computational Linguistics (ACL 2016). Berlin, Germany.
"""
# This file is retrieved from https://github.com/rsennrich/subword-nmt

from __future__ import unicode_literals

import sys
import codecs
import re
import copy
import argparse
import MeCab
from collections import defaultdict, Counter

# hack for python2/3 compatibility
from io import open
argparse.open = open



def get_vocabulary(fobj, is_dict=False):
    """Read text and return dictionary that encodes vocabulary
    """
    vocab = Counter()
    for line in fobj:
        if is_dict:
            word, count = line.strip().split()
            vocab[word] = int(count)
        else:
            for word in line.split():
                vocab[word] += 1
    return vocab


def update_pair_statistics(pair, changed, stats, indices):
    """Minimally update the indices and frequency of symbol pairs

    if we merge a pair of symbols, only pairs that overlap with occurrences
    of this pair are affected, and need to be updated.
    """
    stats[pair] = 0
    indices[pair] = defaultdict(int)
    first, second = pair
    new_pair = first + second
    for j, word, old_word, freq in changed:

        # find all instances of pair, and update frequency/indices around it
        i = 0
        while True:
            # find first symbol
            try:
                i = old_word.index(first, i)
            except ValueError:
                break
            # if first symbol is followed by second symbol, we've found an occurrence of pair (old_word[i:i+2])
            if i < len(old_word) - 1 and old_word[i + 1] == second:
                # assuming a symbol sequence "A B C", if "B C" is merged, reduce the frequency of "A B"
                if i:
                    prev = old_word[i - 1:i + 1]
                    stats[prev] -= freq
                    indices[prev][j] -= 1
                if i < len(old_word) - 2:
                    # assuming a symbol sequence "A B C B", if "B C" is merged, reduce the frequency of "C B".
                    # however, skip this if the sequence is A B C B C, because the frequency of "C B" will be reduced by the previous code block
                    if old_word[i + 2] != first or i >= len(old_word) - 3 or old_word[i + 3] != second:
                        nex = old_word[i + 1:i + 3]
                        stats[nex] -= freq
                        indices[nex][j] -= 1
                i += 2
            else:
                i += 1

        i = 0
        while True:
            try:
                # find new pair
                i = word.index(new_pair, i)
            except ValueError:
                break
            # assuming a symbol sequence "A BC D", if "B C" is merged, increase the frequency of "A BC"
            if i:
                prev = word[i - 1:i + 1]
                stats[prev] += freq
                indices[prev][j] += 1
            # assuming a symbol sequence "A BC B", if "B C" is merged, increase the frequency of "BC B"
            # however, if the sequence is A BC BC, skip this step because the count of "BC BC" will be incremented by the previous code block
            if i < len(word) - 1 and word[i + 1] != new_pair:
                nex = word[i:i + 2]
                stats[nex] += freq
                indices[nex][j] += 1
            i += 1


def get_pair_statistics(vocab):
    """Count frequency of all symbol pairs, and create index"""

    # data structure of pair frequencies
    stats = defaultdict(int)

    # index from pairs to words
    indices = defaultdict(lambda: defaultdict(int))

    for i, (word, freq) in enumerate(vocab):
        prev_char = word[0]
        for char in word[1:]:
            stats[prev_char, char] += freq
            indices[prev_char, char][i] += 1
            prev_char = char

    return stats, indices


def replace_pair(pair, vocab, indices):
    """Replace all occurrences of a symbol pair ('A', 'B') with a new symbol 'AB'"""
    first, second = pair
    pair_str = ''.join(pair)
    pair_str = pair_str.replace('\\', '\\\\')
    changes = []
    pattern = re.compile(
        r'(?<!\S)' + re.escape(first + ' ' + second) + r'(?!\S)')
    if sys.version_info < (3, 0):
        iterator = indices[pair].iteritems()
    else:
        iterator = indices[pair].items()
    for j, freq in iterator:
        if freq < 1:
            continue
        word, freq = vocab[j]
        new_word = ' '.join(word)
        new_word = pattern.sub(pair_str, new_word)
        new_word = tuple(new_word.split())

        vocab[j] = (new_word, freq)
        changes.append((j, new_word, word, freq))

    return changes


def prune_stats(stats, big_stats, threshold):
    """Prune statistics dict for efficiency of max()

    The frequency of a symbol pair never increases, so pruning is generally safe
    (until we the most frequent pair is less frequent than a pair we previously pruned)
    big_stats keeps full statistics for when we need to access pruned items
    """
    for item, freq in list(stats.items()):
        if freq < threshold:
            del stats[item]
            if freq < 0:
                big_stats[item] += freq
            else:
                big_stats[item] = freq
                
def del_post_pos(sentence, m, delete_tag):
    tokens = sentence.split()  # 원본 문장 띄어쓰기로 분리

    dict_list = []

    for token in tokens:  # 띄어쓰기로 분리된 각 토큰 {'단어':'형태소 태그'} 와 같이 딕셔너리 생성
        m.parse('')
        node = m.parseToNode(token)
        word_list = []
        morph_list = []

        while node:
            morphs = node.feature.split(',')
            word_list.append(node.surface)
            morph_list.append(morphs[0])
            node = node.next

        dict_list.append(dict(zip(word_list, morph_list)))

    for dic in dict_list:  # delete_tag에 해당하는 단어 쌍 지우기 (조사에 해당하는 단어 지우기)
        for key in list(dic.keys()):
            if dic[key] in delete_tag:
                del dic[key]

    combine_word = [''.join(list(dic.keys())) for dic in dict_list]  # 형태소로 분리된 각 단어 합치기
    result = ' '.join(combine_word)  # 띄어쓰기로 분리된 각 토큰 합치기

    return result  # 온전한 문장을 반환


def main(infile, outfile, base, num_symbols=32000, _mecab=False, min_frequency=2, verbose=False, is_dict=False):
    """Learn num_symbols BPE operations from vocabulary, and write to outfile.
    """
    
    m = MeCab.Tagger()
    delete_tag = ['BOS/EOS', 'JKS', 'JKC', 'JKG', 'JKO', 'JKB', 'JKV', 'JKQ', 'JX', 'JC']

    # read/write files as UTF-8
    if infile != None:
        infile = codecs.open(infile, encoding='utf-8')
    if outfile != None:
        outfile = codecs.open(outfile, 'w', encoding='utf-8')
        
    with open(f"data/kor_{base}.txt", "w", encoding='utf-8') as f:
        for row in infile:
            if _mecab:
                # mecab추가
                f.write(del_post_pos(row, m, delete_tag))
            else:
                # 기본
                f.write(row)
            f.write('\n')
        f.close()
        
    infile.close()
    infile = codecs.open(f"data/kor_{base}.txt", encoding='utf-8')

    # version 0.2 changes the handling of the end-of-word token ('</w>');
    # version numbering allows bckward compatibility
    outfile.write('#version: 0.2\n')

    vocab = get_vocabulary(infile, is_dict)
    vocab = dict([(tuple(x[:-1]) + (x[-1] + '</w>',), y)
                  for (x, y) in vocab.items()])
    sorted_vocab = sorted(vocab.items(), key=lambda x: x[1], reverse=True)

    stats, indices = get_pair_statistics(sorted_vocab)
    big_stats = copy.deepcopy(stats)
    # threshold is inspired by Zipfian assumption, but should only affect speed
    threshold = max(stats.values()) / 10
    for i in range(num_symbols):     
        if stats:
            most_frequent = max(stats, key=lambda x: (stats[x], x))

        # we probably missed the best pair because of pruning; go back to full statistics
        if not stats or (i and stats[most_frequent] < threshold):
            prune_stats(stats, big_stats, threshold)
            stats = copy.deepcopy(big_stats)
            most_frequent = max(stats, key=lambda x: (stats[x], x))
            # threshold is inspired by Zipfian assumption, but should only affect speed
            threshold = stats[most_frequent] * i / (i + 10000.0)
            prune_stats(stats, big_stats, threshold)

        if stats[most_frequent] < min_frequency:
            sys.stderr.write(
                'no pair has frequency >= {0}. Stopping\n'.format(min_frequency))
            break

        if verbose:
            sys.stderr.write('pair {0}: {1} {2} -> {1}{2} (frequency {3})\n'.format(
                i, most_frequent[0], most_frequent[1], stats[most_frequent]))
        outfile.write('{0} {1}\n'.format(*most_frequent))
        changes = replace_pair(most_frequent, sorted_vocab, indices)
        update_pair_statistics(most_frequent, changes, stats, indices)
        stats[most_frequent] = 0
        if not i % 100:
            prune_stats(stats, big_stats, threshold)

