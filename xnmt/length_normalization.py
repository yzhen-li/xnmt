from numbers import Real
from typing import Sequence, Optional

import numpy as np
from scipy.stats import norm

from xnmt.persistence import serializable_init, Serializable
import xnmt.search_strategy

class LengthNormalization(object):
  '''
  A template class to generate translation from the output probability model.
  '''
  def normalize_completed(self, completed_hyps:'Sequence[xnmt.search_strategy.Hypothesis]', src_length:Optional[int]=None) \
          -> Sequence[float]:
    """
    Apply normalization step to completed hypotheses after search and return the normalized scores.
    
    Args:
      completed_hyps: list of completed Hypothesis objects, will be normalized in-place
      src_length: length of source sequence (None if not given)
    Returns:
      normalized scores
    """
    raise NotImplementedError('normalize_completed must be implemented in LengthNormalization subclasses')
  def normalize_partial(self, score_so_far, score_to_add, new_len):
    """
    Args:
      score_so_far:
      score_to_add:
      new_len: length of output hyp with current word already appended
    Returns:
      new score after applying score_to_add to score_so_far
    normalization step applied during the search
    """
    return score_so_far + score_to_add # default behavior: add up the log probs

class NoNormalization(LengthNormalization, Serializable):
  '''
  Adding no form of length normalization
  '''
  yaml_tag = '!NoNormalization'

  @serializable_init
  def __init__(self):
    pass

  def normalize_completed(self, completed_hyps:'Sequence[xnmt.search_strategy.Hypothesis]', src_length:Optional[int]=None) \
          -> Sequence[float]:
    return [hyp.score for hyp in completed_hyps]

class AdditiveNormalization(LengthNormalization, Serializable):
  '''
  Adding a fixed word penalty everytime the word is added.
  '''
  yaml_tag = '!AdditiveNormalization'

  @serializable_init
  def __init__(self, penalty:Real=-0.1, apply_during_search:bool=False):
    self.penalty = penalty
    self.apply_during_search = apply_during_search

  def normalize_completed(self, completed_hyps:'Sequence[xnmt.search_strategy.Hypothesis]', src_length:Optional[int]=None) \
          -> Sequence[float]:
    if self.apply_during_search:
      return [hyp.score for hyp in completed_hyps]
    else:
      return [hyp.score + (len(hyp.id_list) * self.penalty) for hyp in completed_hyps]
  def normalize_partial(self, score_so_far, score_to_add, new_len):
    return score_so_far + score_to_add + (self.penalty if self.apply_during_search else 0.0)


class PolynomialNormalization(LengthNormalization, Serializable):
  '''
  Dividing by the length (raised to some power)
  '''
  yaml_tag = '!PolynomialNormalization'

  @serializable_init
  def __init__(self, m:Real=1, apply_during_search:bool=False):
    self.m = m
    self.apply_during_search = apply_during_search
    self.pows = []

  def normalize_completed(self, completed_hyps:'Sequence[xnmt.search_strategy.Hypothesis]', src_length:Optional[int]=None) \
          -> Sequence[float]:
    if self.apply_during_search:
      return [hyp.score for hyp in completed_hyps]
    else:
      return [(hyp.score / pow(len(hyp.output.word_ids), self.m)) for hyp in completed_hyps]
  def normalize_partial(self, score_so_far, score_to_add, new_len):
    if self.apply_during_search:
      self.update_pows(new_len)
      return (score_so_far * self.pows[new_len-1] + score_to_add) / self.pows[new_len]
    else:
      return score_so_far + score_to_add
  def update_pows(self, new_len):
    if len(self.pows) < new_len+1:
      for i in range(len(self.pows), new_len+1):
        self.pows.append(pow(i, self.m))


class MultinomialNormalization(LengthNormalization, Serializable):
  '''
  The algorithm followed by:
  Tree-to-Sequence Attentional Neural Machine Translation
  https://arxiv.org/pdf/1603.06075.pdf
  '''
  yaml_tag = '!MultinomialNormalization'

  @serializable_init
  def __init__(self, sent_stats):
    self.stats = sent_stats

  def trg_length_prob(self, src_length, trg_length):
    v = len(self.stats.src_stat)
    if src_length in self.stats.src_stat:
      src_stat = self.stats.src_stat.get(src_length)
      return (src_stat.trg_len_distribution.get(trg_length, 0) + 1) / (src_stat.num_sents + v)
    return 1

  def normalize_completed(self, completed_hyps, src_length=None):
    """
    Args:
      completed_hyps:
      src_length: length of the src sent
    """
    assert (src_length is not None), "Length of Source Sentence is required"

    return [hyp.score + np.log(self.trg_length_prob(src_length, len(hyp.id_list))) for hyp in completed_hyps]


class GaussianNormalization(LengthNormalization, Serializable):
  '''
   The Gaussian regularization encourages the inference
   to select sents that have similar lengths as the
   sents in the training set.
   refer: https://arxiv.org/pdf/1509.04942.pdf
  '''
  yaml_tag = '!GaussianNormalization'

  @serializable_init
  def __init__(self, sent_stats):
    self.stats = sent_stats.trg_stat
    self.num_sent = sent_stats.num_pair
    self.fit_distribution()

  def fit_distribution(self):
    y = np.zeros(self.num_sent)
    iter = 0
    for key in self.stats:
      iter_end = self.stats[key].num_sents + iter
      y[iter:iter_end] = key
      iter = iter_end
    mu, std = norm.fit(y)
    self.distr = norm(mu, std)

  def trg_length_prob(self, trg_length):
    return self.distr.pdf(trg_length)

  def normalize_completed(self, completed_hyps:'Sequence[xnmt.search_strategy.Hypothesis]', src_length:Optional[int]=None) \
          -> Sequence[float]:
    return [hyp.score / self.trg_length_prob(len(hyp.id_list)) for hyp in completed_hyps]
