/*
 * ray.js — Ray's mouth.
 *
 * Ray is a club player, not a chatbot: every line is built from the move that
 * actually happened (piece, squares, capture, check, what the engine wanted)
 * and from the game so far. Nothing here touches the network.
 *
 *   Ray.comment({ san, from, to, piece, captured, check, castle, promo,
 *                 cls, loss, bestSan, bestTo, evalCp, moveNo, accuracy,
 *                 streak })  -> string
 *   Ray.chat(text, ctx)     -> string
 *
 * A shuffle bag per category plus a one-line global memory means he does not
 * repeat himself while a game is going on.
 */
(function (global) {
  'use strict';

  var LAST = '';
  var BAGS = {};

  function shuffled(n) {
    var a = [];
    for (var i = 0; i < n; i++) a.push(i);
    for (var j = a.length - 1; j > 0; j--) {
      var k = Math.floor(Math.random() * (j + 1));
      var t = a[j]; a[j] = a[k]; a[k] = t;
    }
    return a;
  }

  /* Next unused line from a pool; refills only when the pool is exhausted. */
  function pick(pool, key) {
    if (!pool || !pool.length) return '';
    key = key || 'x';
    var bag = BAGS[key];
    if (!bag || !bag.length) bag = BAGS[key] = shuffled(pool.length);
    for (var tries = 0; tries < pool.length; tries++) {
      var idx = bag.pop();
      var line = pool[idx];
      if (line && line !== LAST) { LAST = line; return line; }
      if (!bag.length) bag = BAGS[key] = shuffled(pool.length);
    }
    return pool[0] || '';
  }

  function fill(tpl, c) {
    return String(tpl)
      .replace(/\{san\}/g, c.san || 'that move')
      .replace(/\{from\}/g, c.from || '?')
      .replace(/\{to\}/g, c.to || '?')
      .replace(/\{piece\}/g, c.piece || 'piece')
      .replace(/\{captured\}/g, c.captured || 'piece')
      .replace(/\{bestSan\}/g, c.bestSan || 'something else')
      .replace(/\{bestTo\}/g, c.bestTo || c.to || '?')
      .replace(/\{loss\}/g, c.lossText || 'a bit')
      .replace(/\{eval\}/g, c.evalText || 'level')
      .replace(/\{n\}/g, c.moveNo || '?');
  }

  var BEST = [
    '{san}. that is the move, and i hate how easy you made it look.',
    'engine says {san}, i say {san}. we are not a hive mind, it just happens.',
    '{to} is exactly where that {piece} belongs. clean.',
    'yes. {san}. i was going to suggest it and then pretend i found it first.',
    'best move on the board and you played it without asking me. rude. correct, but rude.',
    'that is the engine\'s first choice. {from} to {to}, no drama.'
  ];
  var EXCELLENT = [
    'not the top pick, but {san} keeps everything. i would have played it.',
    '{san} is the second-best move and it still feels like a plan.',
    'close to best. the engine wanted {bestSan}, you wanted {to}. same neighbourhood.',
    'that is a club player\'s move in the good way. solid, no holes.'
  ];
  var GOOD = [
    '{san} is fine. not a threat, not a gift. chess.',
    'normal move. i have no notes, which is rare for me.',
    'you developed instead of panicking. growth.',
    '{to} is a reasonable square. i would not have thought of it, but reasonable.'
  ];
  var BOOK = [
    'that is theory. i have read one book and it was this one.',
    '{san}. book. i nod knowingly.',
    'opening line. no rating from me, the book already voted.',
    'theory. someone smarter than both of us wrote this down.'
  ];
  var INACC = [
    '{bestSan} was there. {san} is not a disaster, just a little loose.',
    'you left something on the table at {to}. not fatal.',
    'hmm. {san} gives the {piece} a job it is not qualified for.',
    '{from} to {to} is the move i make when i am bored. {bestSan} was better.'
  ];
  var MISTAKE = [
    'that one costs you. {bestSan} first, {san} second.',
    'that one hurts at {to}. i felt it from here.',
    'the {piece} on {to} is not doing what you think it is doing.',
    '{san} — the engine winced. so did i, a little.'
  ];
  var BLUNDER = [
    'ok. {san} is the one we do not put in the group chat. {bestSan} was right there.',
    'that is the kind of move that loses games. {bestSan} was sitting right there.',
    'your {piece} on {to} is asking for trouble and trouble has already said yes.',
    '{from} to {to}? {bestSan}. please. i am begging you, {bestSan}.'
  ];
  var FORCED = [
    'only legal move. no notes. the board chose for you.',
    'one move available. i cannot rate a hostage situation.',
    'forced. we move on.'
  ];
  var CAPTURE = [
    'took the {captured} on {to}. free-ish material, i will take it.',
    'the {captured} is gone. that is one fewer problem.',
    'nice grab on {to}. i pretend i saw it too.'
  ];
  var CHECK = [
    'check. keep squeezing.',
    'that is a check and the king is having a bad day.',
    'check on {to}. do not let him breathe.'
  ];
  var CASTLE = [
    'castled. sensible. i was starting to worry about you.',
    'king tucked away. now the fun part.',
    'castling is the chess equivalent of putting on a seatbelt.'
  ];
  var PROMO = [
    'queened. now please do not give it back.',
    'a new queen on {to}. that is how you end an argument.',
    'promotion. i would like to say i saw this coming. i did not.'
  ];

  var TONE = {
    up: [
      'you are {eval}. do not get cute now.',
      '{eval} for you. finish it properly.',
      'you are winning. i am proud and suspicious.'
    ],
    down: [
      'you are {eval}. it is not over, but it is not funny either.',
      '{eval}. breathe, defend, wait for one mistake.',
      'this is the part where i stop joking. slightly.'
    ],
    level: [
      'dead level. someone has to blink.',
      '{eval}. proper chess, this.',
      'nothing in it. i like your chances, i like his too.'
    ]
  };

  function evalText(cp) {
    if (cp === null || cp === undefined) return 'level';
    var p = cp / 100;
    if (Math.abs(p) < 0.4) return 'level';
    return (p > 0 ? '+' : '') + p.toFixed(1);
  }

  function contextBits(c) {
    var bits = [];
    if (c.castle) bits.push(pick(CASTLE, 'castle'));
    if (c.captured && !c.castle) bits.push(pick(CAPTURE, 'capture'));
    if (c.check) bits.push(pick(CHECK, 'check'));
    if (c.promo) bits.push(pick(PROMO, 'promo'));
    return bits;
  }

  function comment(c) {
    c = c || {};
    var cls = c.only ? 'forced' : (c.cls || 'good');
    var pool = { best: BEST, great: BEST, excellent: EXCELLENT, good: GOOD,
                 book: BOOK, inaccuracy: INACC, mistake: MISTAKE, miss: MISTAKE,
                 blunder: BLUNDER, forced: FORCED }[cls] || GOOD;

    var line = fill(pick(pool, cls), c);
    var extra = contextBits(c);
    if (extra.length && Math.random() < 0.65) line += ' ' + extra[0];

    // stateful, personal touches: streaks, a bad run, a good run
    if (c.streak >= 2 && (cls === 'mistake' || cls === 'blunder' || cls === 'miss')) {
      line += ' that is ' + c.streak + ' in a row. want a hint? no shame in it.';
    } else if (c.streak >= 3 && (cls === 'best' || cls === 'great')) {
      line += ' ' + c.streak + ' good ones running. who are you and what have you done with my student.';
    }

    // eval-aware nudge, only when it says something the move line did not
    if (c.evalCp !== undefined && c.evalCp !== null && Math.random() < 0.45) {
      var e = Math.abs(c.evalCp);
      var tone = e < 40 ? 'level' : (c.evalCp > 0 ? 'up' : 'down');
      if (!(tone === 'up' && cls === 'best') && !(tone === 'down' && (cls === 'blunder' || cls === 'mistake'))) {
        line += ' ' + fill(pick(TONE[tone], 'tone-' + tone), c);
      }
    }
    return line;
  }

  var HELLO = ['hey. {eval}, and you are on move {n}. let us find out which of us is worse.',
               'yo. i have been staring at this position for a while. {eval}.',
               'hey — ray. club player, loud opinions, {eval} on the board.'];
  var THANKS = ['do not thank me, thank the eval bar.', 'appreciate it. now play a move.',
                'we are both doing our best. allegedly.'];
  var BAD = ['yeah. i know. the coach who cannot coach. it is a whole thing.',
             'we are allowed one of those per game. that was it.', 'ok. shake it off. next move.'];
  var ENGINE = ['stockfish underneath, opinions on top. i am the top.',
                'i do not calculate, i agree with the engine very confidently.',
                'the engine tells me the truth, i tell you the truth with worse grammar.'];
  var WHY = ['because the eval bar went that way and i am not brave enough to argue.',
             'stockfish says so and it has never once been polite about it.',
             'honestly? i read the number and then talk like i knew it all along.'];
  var NOTHING = ['ask me about the position, or press hint. i am a coach, not a search box.',
                 'i only know chess and your mistakes. mostly your mistakes.',
                 'mm. play something and i will have a real opinion.'];

  function chat(text, c) {
    c = c || {};
    var t = String(text || '').toLowerCase();
    if (/(why|explain)/.test(t)) return fill(pick(WHY, 'why'), c);
    if (/^(hi|hello|hey|yo)\b/.test(t)) return fill(pick(HELLO, 'hello'), c);
    if (/(rating|elo|how good)/.test(t)) {
      var a = c.accuracy;
      return 'elo is a lie and i do not trust it. ' +
        (a !== null && a !== undefined ? 'you are on ' + a + '% accuracy today.' : 'play some moves and i will tell you.');
    }
    if (/(undo|take ?back|revert)/.test(t)) return 'hit revert. i will not tell anyone.';
    if (/(thanks|ty|nice)/.test(t)) return pick(THANKS, 'thanks');
    if (/(bad|suck|terrible|awful)/.test(t)) return pick(BAD, 'bad');
    if (/(stockfish|engine|bot)/.test(t)) return pick(ENGINE, 'eng');
    if (/(review|analyse|analyze)/.test(t)) return 'finish the game and i will walk through every move. that is the part i am actually good at.';
    return fill(pick(NOTHING, 'nothing'), c);
  }

  global.Ray = { comment: comment, chat: chat, evalText: evalText };
})(typeof window !== 'undefined' ? window : this);
