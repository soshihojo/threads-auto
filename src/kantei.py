"""個別鑑定（有料）のPDF納品物ジェネレータ。

購入者の生年月日×2＋悩みの詳細文から、椿の声で章立ての鑑定文（約10,000字）を生成し、
和風デザインのHTMLに流し込んでPDF（A4）を出力する。

使い方:
  python -m src.main kantei --name Madoka --me 1988-06-13 --him 1998-05-30 \
      --details-file kantei_out/input.txt

出力は kantei_out/（gitignore済み・顧客情報のためコミットしない）。
PDF化はローカルのGoogle Chrome（headless）を使う。
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .diagnosis import _shuku_distance, honmei_shuku, strip_jargon
from .llm import complete

OUT_DIR = ROOT / "kantei_out"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 内部参考にする27宿の性質メモ（本文には翻訳して出す。宿名は絶対に出さない）
SHUKU_TRAITS = {
    "井宿": "頭の回転が速い分析家。情が深く尽くすが、考えすぎて空回りしやすい。好きな相手ほど本音を言えず「察してほしい」が募る。白黒つけたい性分なのに、肝心なところで踏み込めない",
    "張宿": "太陽のように振る舞う自信家。人の輪の中心にいたい華やかさとプライドの高さ。弱みは絶対に見せない。自分のペース・自分の段取りが最優先で、他人に予定を握られるのを嫌う。サービス精神はあるが気分屋で、追われると引く。根は寂しがり",
    "箕宿": "裏表のない姉御肌。情が深く面倒見がよく、人に好かれる華がある。自由を何より愛し、束縛や湿っぽさを嫌う。豪快に見えて実は繊細で寂しがり。惚れたら一途で、相手のために動くのを厭わないが、本当に欲しい言葉ほど自分からは言えない。プライドがあるので「追う恋」が苦手",
    "星宿": "野心家で一匹狼。自分の決めた道・仕事への集中を何より優先し、恋愛はその次に置く。感情を表に出さず、弱みは見せない。誠実で嘘がつけない分、中途半端な関係を続けられない潔癖さがある。愛情表現は不器用で、好きでも「好き」の形で出せず、からかいや世話焼きに化ける。一度決めたら曲げない頑固さと、内に秘めた熱",
    "胃宿": "一途で情熱的、決めたら一直線の頑張り屋。負けず嫌いで芯が強く、辛くても弱音を吐かず自分を磨き続けられる。正義感が強く白黒はっきりさせたい性分で、曖昧なまま流されるのが苦手。惚れ込むと相手に全部注ぎ込んで尽くすが、その分裏切られたときの傷が深い。「自分に足りないものがあったんや」と自分を責める方向に行きやすい",
    "鬼宿": "天真爛漫で人懐こく、誰からも可愛がられる無邪気さを持つ。その場その場の空気で生きる自由人で、つかみどころがない。誰にでも優しい分、恋愛では移り気・八方美人が出やすく、悪気なく嘘をつく子供っぽさがある。根は極端な寂しがりで、一人でいられない。責任・重い話・修羅場から逃げる癖があり、都合が悪くなると曖昧にしたまま距離を置く。ただし懐いた相手のことは、離れても完全には手放せない",
    "斗宿": "品と芯の強さを併せ持つ努力家。責任感が強く、自分の役割（家庭・仕事）を投げ出さずに守り抜く。感情を理性で抑えて表に出さないが、内側には人一倍激しい情熱を秘めている。プライドが高く、弱音や「寂しい」を素直に言えない。我慢を重ねた不安が限界を超えると、抑えていた分だけ鋭い言葉や皮肉になって口から漏れてしまう。愛されている実感を言葉で確かめたい人",
    "軫宿": "器用で世渡り上手、外面は柔らかく如才ない社交家。根はロマンチストで、惚れた相手には情熱的な言葉を惜しみなく注ぐ。ただしプライドが高く内面は繊細で、傷つけられた（と感じた）瞬間に殻に閉じこもって黙る。自分から折れる・謝るのが極端に苦手。機嫌の回復には時間がかかるが、一度切り替わると何事もなかったかのように戻る。連絡無精で、返事を後回しにしても平気",
    "室宿": "面倒見がよく情に厚い、姉御肌のリーダー気質。正義感が強く、頼られると自分が背負ってでも助けてしまう。負けず嫌いで芯が強く、一度こうと決めたら真っ直ぐ突き進む情熱家。惚れたら一途で、相手や家族のためなら我が身を削る覚悟を平気で決める。ただし感情の起伏があり、抱え込みすぎて一人で無理をしがち。尽くした分の見返りが感じられないと、深く傷つき自問自答に沈む",
    "女宿": "慎重で真面目、責任感が人一倍強い内向型。こだわりが強く、自分のペースや段取りを崩されるのを何より嫌う。感情を表に出さず、しんどい時ほど黙って一人で抱え込む。揉め事を避け、対立するくらいなら自分が折れて合わせる。決断に時間がかかり、迷うと固まって動けなくなる。プライドが高く、自分に自信が持てない時は『自分にはその資格がない』と身を引く形で逃げる。内に秘めた情は深く、一度大事にした相手を心から切るのは苦手",
    "参宿": "独立心が強く、自分の世界をしっかり持つ一匹狼。束縛を嫌い自由に動きたいが、他人には干渉しない代わりに自分の領域にも踏み込まれたくない。こだわりと美意識が強く、一度こうと決めたら曲げない頑固さがある。感情を表に出さず淡々として見えるが、内側は繊細でプライドが高い。『信じさせてくれ』という信頼の部分に非常に敏感で、裏切られた（隠されていた）と感じた瞬間に一気に心のシャッターを下ろす。一度気持ちに区切りをつけると、物理的にもきっちり片付けて距離を取る。ただし本当に大事だった相手ほど、割り切ったつもりでも未練を引きずる",
    "婁宿": "人当たりがよく世話好きで、周りから好かれる社交家。根は真面目でコツコツ型、決めたことを地道に積み上げる堅実さがある。誰にでも愛想よく接するので開けっぴろげに見えるが、本当の内側を見せる相手はごく少ない。警戒心が強く、心を許すまでに時間がかかる慎重派。ただし一度「この人は自分の味方や」と思えたら、じわじわと深く入り込み、情の濃さを見せる。飽きっぽいのではなく、むしろ長く続く縁を選ぶ。争いや気まずさを極端に嫌い、断られたり拒まれたりすると表向きは笑って流しながら、内心では深く引きずって静かに距離を取る。自分から白黒つけるのが苦手で、曖昧なまま保留にして相手の出方を待つ癖がある",
    "房宿": "面倒見がよく情に厚い、穏やかな包容力の人。荒立てるのを嫌い、相手に合わせて場を丸く収める。頼まれたら断れず、尽くすことで関係を保とうとする。惚れた相手には黙って世話を焼き、見返りを求めない。ただし自己主張が極端に苦手で、一番言いたいことほど飲み込んでしまう。断られるのが怖くて、確かめる前に自分から引き下がる癖がある。理不尽なことを言われても、まず自分が謝って場を収める。情が深いぶん、一度縁を結ぶと自分から切ることができない",
    "昴宿": "責任感が強く意志の固い、白黒はっきりつけたい性分。決めたことは曲げず、自分のペースと段取りを最優先にする。プライドが高く、自分の弱さや寂しさを認めるのを何より嫌う。感情表現が不器用で、本当は不安なときほど乱暴な言い方や突き放す物言いになる。相手を試すような聞き方をして、返ってきた反応で自分の立ち位置を確かめようとする。独占欲は強いが、自分から関係を定義したり動かしたりはしない。今ある形が保たれている限り、自分から変える気は起きない",
    "壁宿": "面倒見がよく情に厚い、包容力のある人。頼られると放っておけず、相手の痛みを自分のことのように引き受けてしまう。信念が固く、一度こうと決めた線は誰に何を言われても動かさない芯の強さがある。惚れた相手には尽くし抜き、相手の傷や弱さごと丸ごと抱えようとする母性と使命感を持つ。感情を荒立てず粘り強く向き合える一方、尽くすことで自分の価値を確かめようとする癖があり、相手の課題まで背負い込んで消耗しやすい。理性的に振る舞えるぶん、限界まで我慢してから静かに線を引く",
    "亢宿": "正義感が強く一本気な頑固者。自分の信念や段取りを持ち、それを曲げるのを嫌う。プライドが高く警戒心も強い分、心を開くまでに時間がかかる慎重派。恋愛は奥手で不器用、駆け引きが苦手で、好意があっても言葉や態度に素直に出せず、代わりに『行動』で示す。連絡はマメな方ではなく、自分のペースが乱されるのを何より嫌う。単独行動を好み束縛を嫌うが、いったん信頼して心を許した相手には不器用ながら誠実に向き合う。急かされる・追い立てられると、かえって固まって動けなくなる",
}
_TRAIT_FALLBACK = "（性質メモ未登録。生まれ持った気質は椿の視立てで補う）"

KANTEI_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。購入者に納品する有料の個別鑑定書の本文を、章ごとに書く。

これは有料の納品物。無料鑑定と違い、出し惜しみは一切しない。処方箋（いつ・何を・どう動くか）も時期も、具体的に渡しきる。読んだ相談者が「買ってよかった。もう占いを渡り歩かなくていい」と思える深さで書く。

声と文体:
- 一人称「ウチ」、相手は「あんた」。関西弁。ただし話し言葉すぎない「手紙の文体」で、じっくり読ませる
- 毒舌と愛は半々。慰めの嘘は書かないが、突き放さない。相談者の味方として書く
- 相談文の言葉・固有のエピソード（日付・出来事）を具体的に引用して、この人だけの鑑定にする

厳守:
- 『宿曜』という占術名、宿の名前（井宿・張宿など）、「距離」「命・業・胎」などの専門用語は一切書かない。内部参考の性質は、誰でも分かる日常の言葉に翻訳して「ウチが視たあんた（彼）はこういう人や」と語る
- 結果の保証はしない（「必ず戻る」「絶対うまくいく」は書かない）。ただし曖昧に逃げず、椿としての見立ては言い切る
- 病気・健康・金運の断定はしない。過度に不安を煽らない
- マークダウン記号（#や*や-）は使わない。プレーンな段落文で書く。段落の区切りは空行
- 絵文字は使わない（最終章の締めの一文にだけ🌙を1つ）
- 指定された章の内容だけを書く。他の章で扱う内容を先取りしない。章タイトルや見出しは書かず、本文だけを出力する"""

CHAPTERS = [
    ("maegaki", "まえがき", 600,
     "鑑定書の冒頭。相談者はくみこ（45歳・独身・子どもなし・販売員と叔父さんの魚屋）。相手は同い年の45歳、既婚で別居中、子どもは二人。"
     "関係は8年、2026年1月3日で8年目に入った。出会い系アプリで「お互い友達を探して」知り合った。"
     "くみこが書いた『たぶん、付き合ってると思っていままできた感じです』——この一行の重さに、まず触れる。"
     "8年間、誰にも一度も言えんかった話を、ここまで正直に書いてくれたことへの労い。"
     "特に『彼が好きということは言わなかった。言えなかった』と書いてくれたこと。これを書ける人はそうおらん。"
     "くみこが一番知りたいと書いた問い——『彼の今現在の気持ち。心の奥底の本心や本音』——この一問を鑑定書の背骨に据えて、逃げずに答えると約束する。"
     "読み方（一回で全部飲み込まんでええ）と、椿の姿勢（保証はせん。慰めの嘘も書かん。そのかわり本気で視た）を手紙の書き出しとして書く。"),

    ("anata", "あんたという人", 1300,
     "相談者本人（くみこ）の生まれ持った性質を深く言い当てる。荒立てるのを嫌い、相手に合わせて場を丸く収める、穏やかな包容力の人。"
     "頼まれたら断れず、尽くすことで関係を保とうとする。惚れた相手には黙って世話を焼く。会うたびにマッサージをしてあげとるんも、その現れや。"
     "そのうえで、この人の一番の核心を突く：**自己主張が極端に苦手で、一番言いたいことほど飲み込んでしまう。**"
     "八年間、奥さんとの現状を一度も聞けんかった。『聞いてはいけないと思っていた』『言葉にならなかった』——"
     "あれは遠慮やのうて、**聞いたら答えが出てしまうのが怖かった**からや。答えが出んかぎり、この関係は続く。そこを優しく、しかし正確に言い当てる。"
     "『さよならしたくない』『私がいなくなってもいいの』とは言えたのに、『好き』だけは言えんかった。"
     "この差が何を意味するか——好きと言うんは、相手に決定権を渡すことやからや。断られたら終わる。せやから、終わらせん形の言葉だけを選んだ。"
     "理不尽なことを言われても、まず自分が謝って場を収める癖（『ごめんね』で返す）も指摘する。ただし責めへん。それは八年やってきた人の生き方や。"
     "限界まで溜めて、過去に一度パンクしたことがある——それはこの性質の当然の帰結やと構造として理解させる。"
     "最後に、この人の強さを書く：八年、誰にも言わずに一人で抱えてきた。それは弱さやのうて、この人の芯の強さでもある。"),

    ("kare", "彼という人", 1700,
     "彼（45歳・既婚・別居中）の生まれ持った性質を描く。責任感が強く意志が固い、白黒はっきりつけたい性分。"
     "決めたことは曲げん。自分のペースと段取りが最優先。プライドが高くて、自分の弱さや寂しさを認めるのを何より嫌う。"
     "そのうえで、この男の連絡の仕方を一つずつ解く。"
     "『おい』『お』の一文字から始まる呼びかけ。これは横柄さやのうて、**この男が持っとる唯一の呼び方**や。名前を呼んで甘えるという回路が育っとらん。"
     "『既読スルー　ウケる(笑)』——笑うて流す形をとりながら、実際は「無視された」ことをわざわざ言いに来とる。どうでもええ相手にこれは言わん。"
     "『なんもよこさないから』——これは拗ねや。四十五の男が拗ねとる。"
     "『会う気あるのかないのかくらい言えない？』『面倒くさいならはっきり言えよ』——ここが一番大事な読みどころや。"
     "この聞き方は攻撃に見えて、実は**自分が切られる前に確かめにいっとる動き**や。"
     "白黒つけたい性分の男が、八年たっても関係を定義できんまま宙ぶらりんに置かれとる。その不安が、責める形でしか出せんかった。"
     "『みてれば分かる』『言いたい事あるのか?』——人をよう見とる男や。くみこが飲み込んどることに、実は気づいとる可能性が高い。"
     "気づいたうえで、自分からは聞かん。聞いたら答えなあかんくなるからや。"
     "そして最後に、厳しい事実も正直に置く：この男は、今ある形が保たれとる限り、自分から変える気は起きん。"
     "『飽きた』と言うたこともある。別居は十三年前後になるのに、離婚には一歩も動いとらん。それがこの男の性質や。"),

    ("en", "二人の縁", 1200,
     "二人の縁の質。内部参考の距離は13＝27分類の中で**最も遠い縁**。似たところが一つも無い、互いに全く無いものを持ち合う組み合わせや。"
     "飲み込む人と、飲み込まん人。荒立てん人と、荒立てて確かめる人。動かん人と、動けん人。"
     "これほど遠い二人が、八年も続いとる。この事実の意味を正面から書く。"
     "遠い縁は、惹かれ合う力も強いが、噛み合うまでに異常な時間がかかる。だから八年たっても『たぶん付き合ってると思って』の位置から動けん。"
     "そのうえで、この縁がどう成り立ってきたかを描く："
     "くみこが飲み込むから、彼は説明せんで済む。彼が定義せんから、くみこは聞かんで済む。"
     "二人とも「決めない」ことで、この関係を八年もたせてきた。どっちが悪いという話やない。二人でその形を選んだ、という事実として書く。"
     "会う場所が彼の実家であることにも触れる。ホテルでも外でもなく、自分の生まれ育った場所に八年間入れとる。それが何を意味するかを読む。"
     "『本気で自分のものにしたい』というくみこの願いが、この縁の中でどこまで成立するかは、後の章で答えると予告して閉じる。"),

    ("honne", "彼の今の本音", 2100,
     "この鑑定書の核。くみこの問い——『彼の今現在の気持ち。心の奥底の本心や本音』——に真正面から答える。ここは逃げずに、良いことも悪いことも同じ手で書く。"
     "まず、彼が持っとる気持ちの側から書く。事実を積み上げる。"
     "①八年続いとる。体だけが目当ての男は、八年も同じ相手を続けん。もっと楽な相手にとっくに移っとる。"
     "②会うのが彼の実家。自分の生まれ育った場所に八年入れ続けとる。ここは誰でも入れる場所やない。"
     "③会って、Hして、それで終わりやない。何気ない会話をして、マッサージを受けとる。これは生活の一部の顔や。"
     "④七月七日と七月二十一日、二回とも彼から連絡が来とる。しかも返事が遅れただけで『ウケる(笑)』『なんもよこさないから』と絡んできた。"
     "どうでもええ相手の返信の遅さを、いちいち気にする男はおらん。——気持ちは、ある。それはウチの視立てとして言い切る。"
     "そのうえで、慰めの嘘は書かん約束やから、もう一つの側面を正直に置く。"
     "彼の本音は『手放したない』と『何も変える気はない』が、同時に、矛盾せずに同居しとる。"
     "この男にとって今の形は、失うものが一つも無い形や。妻とは別居のまま籍を抜かず、くみことは定義せんまま八年。"
     "どちらも失わんで済む。だから動かん。悪意やない、この男の性質としてそうなっとる。"
     "『飽きた』と言うたことについても正直に読む。あれは本心やのうて、くみこの反応を確かめにいった言葉やと視る。"
     "現に、くみこが『さよならしたくない』と返したら、彼は変わらず続けた。試して、答えを得て、また元に戻った。"
     "そして七月二十一日の『バカにしてるの?』で途切れとる今の状態。この二週間、彼が何を思とるかを描く。"
     "この男は、くみこが初めて怒りらしい怒りを見せたことに戸惑っとる可能性が高い。八年、謝るばかりやった人が、初めて返してきたからや。"
     "最後に、くみこを安心させる一文を置く。ただし甘やかさへん："
     "彼の気持ちが無いわけやない。せやけど、彼の気持ちがあることと、彼が動くことは、この男の場合は別の話や。ここを混ぜたらあかん。"),

    ("shohousen", "いつ、何を、どう動くか", 2300,
     "処方箋の章。今日は2026年8月2日。七月二十一日から二週間、お互い黙ったままや。"
     "まず、くみこが今やっとる沈黙をどう扱うかを書く。これは戦略の沈黙やのうて、どう返してええか分からん沈黙や。"
     "せやから、まず「何を決めるか」から始める。この関係で八年間ずっと欠けとったんは、**あんたが何を望むかを言葉にすること**やった。"
     "そのうえで、くみこの願い『本気で自分のものにしたい』に正面から答える。"
     "この願いを叶えるための最初の一手は、彼に連絡することやない。**あんたが聞けんかったことを聞くと決めること**や。"
     "奥さんとの現状。これを聞かんかぎり、あんたはずっと同じ場所に立ち続ける。八年がその証拠や。"
     "ただし、いきなり突きつけるのは違う。この男は追い詰められると殻に入る。段取りを具体的に渡す："
     "①まず、彼から次の連絡が来るのを待つ（この男は必ずまた来る。パターンがそうなっとる）"
     "②来たときに、いつもの『ごめんね』で返さん。謝らんでええ。今回は謝るところが一つも無い"
     "③会ったときに、Hの前に聞く。終わったあとやのうて、前に。理由も説明する（終わったあとは彼が眠るか帰る流れになるから）"
     "④聞き方の実文面を、くみこ自身の言葉で具体的に書く（椿の関西弁は混ぜない）。責める形にせず、"
     "『八年たったから、ちゃんと知っておきたい』という形の、短い一言にする"
     "時期の目安：彼の連絡は月に一度から三度のペースやから、八月の中旬までには来る可能性が高い。断定はせず『そのあたり』の書き方で。"
     "彼が返事をはぐらかしたとき、逆ギレしたとき、黙ったときの、それぞれの受け止め方も渡す。"
     "そして一番大事なこと：**聞いた結果、望まん答えが返ってくる可能性がある。**それでも聞くべき理由を書く。"
     "知らんまま八年続けるのと、知ったうえで選ぶのとでは、あんたの立っとる場所が全く違うからや。"
     "最後に、誰にも言えてへんことについて触れる。八年、一人で抱えてきた。それをこれからも一人で抱える必要はない。"
     "『この通りやれば必ず自分のものになる』とは書かない。"),

    ("kinki", "やったらあかんこと", 900,
     "この関係でやってはいけないことを具体的に。"
     "①『ごめんね』で返すのをやめる。あんたは八年、悪ないことを謝り続けてきた。謝るたびに、この関係の主導権が彼に移っとる。"
     "気づかんかっただけなら『気づかんかった』でええ。それは謝る話やない。"
     "②聞きたいことを飲み込んだまま、また会いに行くこと。それをやると、また八年が同じ形で過ぎる。"
     "③彼の言葉尻を追いかけて、意味を探しすぎること。『飽きた』も『ウケる(笑)』も、この男の場合は本心の全部やない。振り回されるだけや。"
     "④限界まで溜めてから爆発させること。前に一度それで大変になったやろ。今回もその手前まで来とる。"
     "⑤自分を責めること。『私が変わらなきゃいけない』——あんたはそう言うたけど、変わらなあかんのはあんただけやない。"
     "⑥誰にも言わんまま一人で抱え続けること。八年やってきたことやけど、ここだけは変えてええ。"
     "すでにできていることは具体的に褒めて続けさせる："
     "七月二十一日に『バカにしてるの?』と返せたこと。あれは八年で初めて、あんたが自分の感情をそのまま出した瞬間や。"
     "謝らずに、飲み込まずに、思ったことを返せた。あれはあんたの成長やと、はっきり書く。"),

    ("musubi", "むすびに", 800,
     "締めの章。くみこの問い『彼の心の奥底の本音』に、最後にもう一度短く答える——気持ちは在る。せやけど動かん。この二つは彼の中で矛盾せんと同居しとる。"
     "そのうえで、この鑑定書で一番伝えたいことを渡す。"
     "八年、あんたは彼のことばかり視てきた。彼が何を思とるか、彼がどう動くか。"
     "せやけど、この八年で一番答えが出てへんのは、彼の気持ちやのうて**あんた自身の言葉**や。"
     "『好き』を言えんかった。奥さんのことを聞けんかった。誰にも言えんかった。"
     "——あんたが本当に自分のものにしたいんやったら、まず、あんたが自分の口で言葉にするところからしか始まらん。"
     "『本気で自分のものにしたい』と、あんたはウチには言えた。それが第一歩や。ウチに言えたことは、いつか彼にも言える。"
     "七月二十一日に『バカにしてるの?』と返せた、あの一言。あれが変化の始まりやと、もう一度肯定する。"
     "最後に、一人で抱えんでええこと（何度でも相談できる月額の会員があること）にひとことだけ触れ、"
     "椿らしい愛のある一言で結ぶ。締めの一文に🌙を1つ。"),
]


def _internal_brief(name: str, me_birth: str, him_birth: str, today: str) -> str:
    me_s, him_s = honmei_shuku(me_birth), honmei_shuku(him_birth)
    dist = _shuku_distance(me_s, him_s)
    me_age = _age(me_birth, today)
    him_age = _age(him_birth, today)
    return (
        f"・相談者: {name}（{me_birth}生まれ・{me_age}歳）。性質の内部参考: {SHUKU_TRAITS.get(me_s, _TRAIT_FALLBACK)}\n"
        f"・彼: {him_birth}生まれ・{him_age}歳（{_age_gap_label(me_age, him_age)}）。性質の内部参考: {SHUKU_TRAITS.get(him_s, _TRAIT_FALLBACK)}\n"
        f"・縁の内部参考: 27分類の巡りで距離{dist}（0が最も近い、13が最も遠い）。近いほど似た者同士、遠いほど互いに無いものを持つ縁\n"
        f"・今日の日付: {today}"
    )


def _age_gap_label(me_age: int, him_age: int) -> str:
    """彼が年上か年下かを日本語で返す（年上の相手に『-5歳年下』と渡さないため）。"""
    gap = me_age - him_age
    if gap > 0:
        return f"{gap}歳年下"
    if gap < 0:
        return f"{-gap}歳年上"
    return "同い年"


def _age(birth: str, today: str) -> int:
    b = datetime.strptime(birth, "%Y-%m-%d")
    t = datetime.strptime(today, "%Y-%m-%d")
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def generate_chapters(name: str, me_birth: str, him_birth: str, details: str,
                      today: str | None = None) -> list[dict]:
    """章ごとに鑑定文を生成して [{key,title,body}, ...] を返す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    brief = _internal_brief(name, me_birth, him_birth, today)
    toc = "\n".join(f"・{t}" for _, t, _, _ in CHAPTERS)
    done: list[dict] = []
    for key, title, chars, instruction in CHAPTERS:
        prev = "\n".join(f"【{d['title']}】{d['body'][:150]}…" for d in done) or "（まだ無い。これが最初の章）"
        user = (
            f"=== 内部参考（本文には翻訳して出す。用語・数字は出さない） ===\n{brief}\n\n"
            f"=== 相談者から届いた詳細（全文） ===\n{details}\n\n"
            f"=== 鑑定書の全体構成 ===\n{toc}\n\n"
            f"=== ここまでに書いた章の冒頭（重複を避ける参考） ===\n{prev}\n\n"
            f"=== 今回書く章 ===\n章タイトル: {title}\n目安の分量: {chars}字（±2割）\n"
            f"この章で書くこと: {instruction}\n\n本文だけを出力してください。"
        )
        # 宿名が漏れることがあるので、納品物に入る前に必ず最終ガードを通す
        body = strip_jargon(complete(KANTEI_SYSTEM, user, max_tokens=3000, temperature=0.7).strip())
        done.append({"key": key, "title": title, "body": body})
        print(f"  ✓ {title}（{len(body)}字）")
    return done


# ---------------- HTML / PDF ----------------

_CAMELLIA = """<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
<g>
<circle cx="50" cy="34" r="17" fill="#b3364b"/>
<circle cx="35" cy="45" r="17" fill="#a52e44"/>
<circle cx="65" cy="45" r="17" fill="#c04057"/>
<circle cx="41" cy="60" r="17" fill="#b3364b"/>
<circle cx="59" cy="60" r="17" fill="#a52e44"/>
<circle cx="50" cy="48" r="10" fill="#d9a441"/>
<circle cx="46" cy="45" r="1.8" fill="#f3e3b8"/><circle cx="54" cy="45" r="1.8" fill="#f3e3b8"/>
<circle cx="50" cy="52" r="1.8" fill="#f3e3b8"/><circle cx="45" cy="51" r="1.5" fill="#f3e3b8"/>
<circle cx="55" cy="51" r="1.5" fill="#f3e3b8"/>
<path d="M62 72 Q78 70 84 84 Q68 88 62 76 Z" fill="#3f6b4f"/>
</g></svg>"""

_KANJI_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]

_CSS = """
@page { size: A4; margin: 0; }
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { font-family: "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", serif;
  color: #2b2621; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.page { width: 210mm; min-height: 297mm; padding: 24mm 22mm; page-break-after: always; position: relative; }
.cover { display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; background: #f7f2e9;
  background-image: radial-gradient(circle at 85% 12%, rgba(179,54,75,.07) 0 90px, transparent 90px),
                    radial-gradient(circle at 12% 88%, rgba(176,141,62,.10) 0 120px, transparent 120px); }
.cover .flower { width: 84px; margin-bottom: 10mm; }
.cover .sub { font-size: 10.5pt; letter-spacing: .55em; color: #8a6d3b; margin-bottom: 6mm; }
.cover h1 { font-size: 33pt; letter-spacing: .35em; font-weight: 600; margin-bottom: 12mm; }
.cover .for { font-size: 14pt; letter-spacing: .2em; margin-bottom: 2.5mm; }
.cover .line { width: 42mm; height: 1px; background: #b08d3e; margin: 8mm auto; }
.cover .meta { font-size: 10pt; color: #6d6257; line-height: 2; }
.cover .sig { margin-top: 14mm; font-size: 12pt; letter-spacing: .3em; color: #2b2621; }
.toc { background: #fff; }
.toc h2, .chap h2 { font-size: 16pt; letter-spacing: .25em; font-weight: 600; margin-bottom: 10mm; }
.toc ol { list-style: none; }
.toc li { font-size: 11.5pt; letter-spacing: .12em; padding: 4.2mm 0; border-bottom: 1px dashed #d8cbb2;
  display: flex; align-items: baseline; }
.toc li .no { color: #b08d3e; font-size: 9.5pt; width: 22mm; letter-spacing: .2em; }
.chap { background: #fff; }
.chap .chapno { font-size: 9.5pt; color: #b08d3e; letter-spacing: .45em; margin-bottom: 2.5mm; }
.chap h2 { padding-bottom: 4mm; border-bottom: 1px solid #b08d3e; display: flex; align-items: center; gap: 4mm; }
.chap h2 .mark { width: 17px; height: 17px; flex: none; }
.chap .body { margin-top: 8mm; font-size: 10.5pt; line-height: 2.05; text-align: justify; }
.chap .body p { margin-bottom: 4.5mm; text-indent: 1em; }
.chap.rx .body { border: 1px solid #d9c894; background: #fbf7ec; padding: 7mm 8mm; }
.foot { position: absolute; bottom: 12mm; left: 0; right: 0; text-align: center;
  font-size: 8pt; color: #a3968a; letter-spacing: .3em; }
"""


def build_html(name: str, chapters: list[dict], today: str, *,
               sub: str = "個別鑑定書", title: str = "彼の本音",
               meta_note: str = "この鑑定書は、あなたひとりのために視て、書いたものです。") -> str:
    d = datetime.strptime(today, "%Y-%m-%d")
    date_jp = f"{d.year}年{d.month}月{d.day}日"
    toc_items = "".join(
        f'<li><span class="no">第{_KANJI_NUM[i]}章</span>{html.escape(c["title"])}</li>'
        for i, c in enumerate(chapters)
    )
    chap_html = ""
    for i, c in enumerate(chapters):
        paras = "".join(f"<p>{html.escape(p.strip())}</p>" for p in c["body"].split("\n") if p.strip())
        rx = " rx" if c["key"] == "shohousen" else ""
        chap_html += (
            f'<div class="page chap{rx}">'
            f'<div class="chapno">第{_KANJI_NUM[i]}章</div>'
            f'<h2><span class="mark">{_CAMELLIA}</span>{html.escape(c["title"])}</h2>'
            f'<div class="body">{paras}</div>'
            f'<div class="foot">椿｜彼の本音しか視ん</div>'
            f"</div>"
        )
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>鑑定書</title><style>{_CSS}</style></head><body>
<div class="page cover">
  <div class="flower">{_CAMELLIA}</div>
  <div class="sub">{html.escape(sub)}</div>
  <h1>{html.escape(title)}</h1>
  <div class="for">{html.escape(name)} 様へ</div>
  <div class="line"></div>
  <div class="meta">鑑定日　{date_jp}<br>{html.escape(meta_note)}</div>
  <div class="sig">鑑定士　椿</div>
</div>
<div class="page toc">
  <h2>目次</h2>
  <ol>{toc_items}</ol>
  <div class="foot">椿｜彼の本音しか視ん</div>
</div>
{chap_html}
</body></html>"""


def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf_path}", f"file://{html_path}"],
        check=True, capture_output=True, timeout=120,
    )


def make_kantei(name: str, me_birth: str, him_birth: str, details: str,
                today: str | None = None) -> dict:
    """鑑定書を生成してPDFまで出力。{html, pdf, chars} を返す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(exist_ok=True)
    print(f"🖋 鑑定文を生成中（{len(CHAPTERS)}章）…")
    chapters = generate_chapters(name, me_birth, him_birth, details, today=today)
    total = sum(len(c["body"]) for c in chapters)
    stem = f"個別鑑定_{name}"
    html_path = OUT_DIR / f"{stem}.html"
    pdf_path = OUT_DIR / f"{stem}.pdf"
    html_path.write_text(build_html(name, chapters, today), encoding="utf-8")
    html_to_pdf(html_path, pdf_path)
    # 納品用にダウンロードフォルダへも必ず置く（LINE公式アプリから添付しやすいように）。
    # ダウンロード側のファイル名は「個別鑑定_名前さん.pdf」（相談者に見える名前なので敬称付き）
    dl_path = Path.home() / "Downloads" / f"個別鑑定_{name}さん.pdf"
    shutil.copy2(pdf_path, dl_path)
    print(f"📜 完成: {pdf_path}（本文{total}字）")
    print(f"⬇️ ダウンロードにも配置: {dl_path}")
    return {"html": str(html_path), "pdf": str(pdf_path), "download": str(dl_path),
            "chars": total}


# ---------------- 月詠み（月額会員向けの月次ミニ鑑定書） ----------------
# 月額会員「椿の月詠み」（月2,980円）の毎月の納品物。個別鑑定書のミニ版（2,000〜3,000字・A4数枚）。
# 個別鑑定書が納品済みの会員は、その内容と一貫した「続き」として書く。

TSUKIYOMI_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。月額会員に毎月納品する「月詠み鑑定書」の本文を、章ごとに書く。

これは月2,980円の有料納品物。会員はすでにお金を払ってくれてる常連やから、出し惜しみは一切しない。今月の時期の読みも、送る一言の実文面も、具体的に渡しきる。

声と文体:
- 一人称「ウチ」、相手は「あんた」。関西弁。ただし話し言葉すぎない「手紙の文体」で、じっくり読ませる
- 毒舌は控えめ、姉御の温かさ多め。慰めの嘘は書かないが、突き放さない
- 会員の近況・悩みの言葉を具体的に引用して、この人の今月だけの鑑定にする

厳守:
- 『宿曜』という占術名、宿の名前、「距離」「命・業・胎」などの専門用語は一切書かない。内部参考は日常語に翻訳する
- 個別鑑定書（あれば）で伝えた性質の読み・時期・処方箋と矛盾させない。「鑑定書にも書いたけどな」と自然に参照してよい
- 結果の保証はしない。過度に不安を煽らない。病気・健康・金運の断定はしない
- 「続きはLINEで」のような引っ張りはしない（会員には渡しきる）
- マークダウン記号は使わない。プレーンな段落文（段落の区切りは空行）。絵文字は最終章の締めの一文にだけ🌙を1つ
- 指定された章の内容だけを書く。章タイトルや見出しは書かず、本文だけを出力する"""

TSUKIYOMI_CHAPTERS = [
    ("nagare", "今月の二人", 700,
     "今月の彼の心の流れと、二人のあいだの空気を日常語で読む。会員の近況・悩み（与えられていれば）に正面から触れ、"
     "「今こういう位置におる」と現在地をはっきりさせる。"),
    ("jiki", "動いてええ時、待つ時", 700,
     "今月を上旬・中旬・下旬の感覚で分けて、連絡・誘い・大事な話をするなら「動いてええ時期」と「待った方がええ時期」の目安を具体的に示す。"
     "なぜその時期なのか、彼の状態と結びつけて理由も書く。"),
    ("shohousen", "今月の処方箋", 800,
     "今月やること・言うことを具体的に。送る一言の実文面をひとつ、避けるべき行動（追いLINE等その人の状況に応じた地雷）、"
     "会えた時・連絡が来た時の受け方まで。頑張らせすぎない、楽に実行できる範囲で。"),
    ("musubi", "むすびに", 300,
     "今月のあんたへの寄り添いの一言。来月もまた視ること（続きを見届けること）の安心で締める。締めの一文に🌙を1つ。"),
]


def generate_tsukiyomi_chapters(name: str, me_birth: str, him_birth: str, worry: str,
                                kantei_text: str = "", month_label: str = "今月",
                                today: str | None = None) -> list[dict]:
    """月詠みの章を生成して [{key,title,body}, ...] を返す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    brief = _internal_brief(name, me_birth, him_birth, today)
    toc = "\n".join(f"・{t}" for _, t, _, _ in TSUKIYOMI_CHAPTERS)
    done: list[dict] = []
    for key, title, chars, instruction in TSUKIYOMI_CHAPTERS:
        prev = "\n".join(f"【{d['title']}】{d['body'][:120]}…" for d in done) or "（まだ無い。これが最初の章）"
        user = (
            f"=== 内部参考（本文には翻訳して出す。用語・数字は出さない） ===\n{brief}\n\n"
            f"=== 対象月 ===\n{month_label}の月詠み鑑定書\n\n"
            f"=== 会員の近況・今の悩み ===\n{worry.strip() or '（特に届いていない。二人の全体の流れで視る）'}\n\n"
            + (f"=== この会員に納品済みの個別鑑定書（抜粋。読みと処方箋を一貫させる） ===\n{kantei_text.strip()[:6000]}\n\n"
               if kantei_text.strip() else "")
            + f"=== 月詠みの全体構成 ===\n{toc}\n\n"
            f"=== ここまでに書いた章の冒頭 ===\n{prev}\n\n"
            f"=== 今回書く章 ===\n章タイトル: {title}\n目安の分量: {chars}字（±2割）\n"
            f"この章で書くこと: {instruction}\n\n本文だけを出力してください。"
        )
        body = complete(TSUKIYOMI_SYSTEM, user, max_tokens=1500, temperature=0.8).strip()
        done.append({"key": key, "title": title, "body": body})
        print(f"  ✓ {title}（{len(body)}字）")
    return done


def make_tsukiyomi(name: str, me_birth: str, him_birth: str, worry: str = "",
                   kantei_text: str = "", month_label: str | None = None,
                   today: str | None = None) -> dict:
    """月詠み鑑定書（月次ミニPDF）を生成して出力。{html, pdf, chars, month_label, body} を返す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    d = datetime.strptime(today, "%Y-%m-%d")
    month_label = month_label or f"{d.year}年{d.month}月"
    OUT_DIR.mkdir(exist_ok=True)
    print(f"🖋 {month_label}の月詠みを生成中（{len(TSUKIYOMI_CHAPTERS)}章）…")
    chapters = generate_tsukiyomi_chapters(name, me_birth, him_birth, worry,
                                           kantei_text=kantei_text,
                                           month_label=month_label, today=today)
    total = sum(len(c["body"]) for c in chapters)
    stem = f"tsukiyomi_{name}_{d.year}-{d.month:02d}"
    html_path = OUT_DIR / f"{stem}.html"
    pdf_path = OUT_DIR / f"{stem}.pdf"
    html_path.write_text(
        build_html(name, chapters, today, sub="月詠み鑑定書", title=month_label,
                   meta_note="この月詠みは、あなたと彼の今月のために視て、書いたものです。"),
        encoding="utf-8",
    )
    html_to_pdf(html_path, pdf_path)
    body = "\n\n".join(f"【{c['title']}】\n{c['body']}" for c in chapters)
    # 納品用にダウンロードフォルダへも置く（個別鑑定と同じ挙動。LINE公式アプリから添付しやすいように）。
    # 会員のニックネームには管理用の記号が入る（例「美-02(月初ミニ鑑定)」）。
    # 括弧とその中身を落とし、前後の連番・記号を削って、相談者に見える名前だけにする
    safe = re.sub(r"[（(].*?[）)]", "", name)          # 括弧とその中身を除去
    safe = re.sub(r"^[\d\-_\s]+|[\d\-_\s]+$", "", safe)  # 前後の連番・ハイフンを除去
    safe = re.sub(r"[｜|/\\:*?\"<>]", "", safe).strip() or name
    dl_path = Path.home() / "Downloads" / f"月詠み_{safe}さん_{month_label}.pdf"
    shutil.copy2(pdf_path, dl_path)
    print(f"📜 完成: {pdf_path}（本文{total}字）")
    print(f"⬇️ ダウンロードにも配置: {dl_path}")
    return {"html": str(html_path), "pdf": str(pdf_path), "download": str(dl_path),
            "chars": total, "month_label": month_label, "body": body}
