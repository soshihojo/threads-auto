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
     "鑑定書の冒頭。相談者は未来（27歳）。相手は同い年の柔道整復師。高校3年のバイト先で出会い、8年ちょっと付き合って、2025年7月に別れた。今日でちょうど一年。"
     "深夜に、金の貸し借りのことまで包み隠さず書いてくれたことへの労い。"
     "特に『人としてはめちゃくちゃムカつく』『他の人ならすぐ忘れられるのに何故か残ってる』『復縁したいわけでもないけど、無しとも言い切れん』——"
     "この中途半端さを、飾らずそのまま言葉にできる人はそうおらん。あれが一番の材料になったと伝える。"
     "そして最重要：**この鑑定書は『復縁するための本』やない。**未来が書いた通り、"
     "『自分が納得のいけるように向き合って、それでも戻れるなら戻りたい。お互い向き合えないなら終わりでスッキリする』——"
     "この一文を背骨に据える。戻る道と、終わる道、その両方を最後まで書き切ると約束する。"
     "読み方（一回で全部飲み込まんでええ）と、椿の姿勢（保証はせん。慰めの嘘も書かん）を手紙の書き出しとして書く。"),

    ("anata", "あんたという人", 1300,
     "相談者本人（未来）の生まれ持った性質を深く言い当てる。器用で外面は柔らかく、人付き合いが如才ない社交家。"
     "根はロマンチストで、惚れた相手には情熱を注ぐ。ただしプライドが高く内面は繊細で、傷つけられたと感じた瞬間に殻に閉じて黙る。"
     "自分から折れる・謝るのが極端に苦手。——これが今回の別れ方にそのまま出とる。"
     "十八の頃から二十六まで、人生のほぼ全部をあの男と過ごしてきた。高校のバイト先から八年。"
     "『他の人ならすぐ忘れられる』のに忘れられんのは、**あの男が特別やからやのうて、あんたの人生の骨組みそのものやったから**や、と言い当てる。"
     "十代の記憶も、二十代前半の記憶も、全部そこに彼が写り込んどる。それを消すいうんは、自分の八年を消すのと同じやからしんどい。"
     "そのうえで、この人の一番の落とし穴を突く：**白黒つけたいのに、自分からは確かめにいけへん。**"
     "『彼女ができたからかな』と思っても、聞かんかった。ブロックされても、理由を聞きにいかんかった。"
     "傷つく前に自分から引く癖がある。それは弱さやのうて、あんたなりの守り方やったと理解を示したうえで、"
     "その守り方が今、あんたを一年止めとる、と正直に書く。"),

    ("kare", "彼という人", 1700,
     "彼（27歳・柔道整復師）の生まれ持った性質を描く。一途で情熱的、決めたら一直線の頑張り屋。負けず嫌いで芯が強い。"
     "国家資格を取って人の体を治す仕事に就いとる。そこは正当に評価する。中途半端な男には務まらん仕事や。"
     "白黒はっきりさせたい性分で、曖昧なまま流されるのが苦手——**なのに、この男は今回、全部を曖昧にしたまま逃げとる。**この矛盾を軸に読む。"
     "『一旦別れよう』——白黒つけたい性分の男が、なんで『一旦』なんて含みを残したんか。"
     "それは、切りたないけど、結婚という具体に耐えられんかったからや。決めたら一直線に進む男が、進む先を見て足がすくんだ。"
     "別れたあとも会うてご飯食べとったんも同じ理屈や。手放したないから会う。せやけど責任は負われへんから、じわじわ離れる。"
     "この男の逃げ方は、**はっきり終わらせずにフェードアウトする**形やと言い当てる。"
     "そして金のこと。三万ずつ借りては返す——これを繰り返しとった意味を読む。"
     "返しとる時点で踏み倒す気はない。せやけど『借りる』いう行為は、別れた相手と繋がり続ける口実にもなる。"
     "本人が意識しとるかは別として、**金の貸し借りが、切れかけた線を繋ぎ止める最後の糸になっとった**と視る。"
     "その糸を『お金は貸せない』で切られた。返ってきたんは『わかった』の四文字と、ブロック。"
     "負けず嫌いで、辛くても弱音を吐かん男や。『自分に足りひんもんがあったんや』と自分を責める方に行きやすい性質でもある。"
     "だからブロックは、嫌いになった証やのうて、**これ以上みっともない自分を見られたくないという遮断**やと読む。"),

    ("en", "二人の縁", 1200,
     "二人の縁の質。内部参考の距離は11＝かなり遠い縁。似た者同士やのうて、互いに無いものを持ち合う組み合わせや。"
     "外面が柔らかくて器用な未来と、不器用で一直線な彼。折れられん未来と、白黒つけたいのに曖昧にする彼。"
     "遠い縁は、惹かれ合う力も、相手を変える力も強い。せやから八年ももった。そのかわり、噛み合わんまま長引くと消耗も大きい。"
     "高校三年のバイト先から八年——この縁は『育った縁』や。恋人である前に、互いの人生の土台になってしもてる。"
     "だからこそ、別れ方が難しかった。恋人としては終われても、**土台としては終われん**。それが今のモヤモヤの正体の一つやと書く。"
     "結婚の話し合いですれ違った意味も読む。八年一緒におった二人が、初めて『これから』を具体的に話した時に壊れた。"
     "それは相性が悪かったからやない。**二人とも、その話を初めてしたから**や。慣れとらんことをやって、下手やっただけ。"
     "そのうえで正直に書く：この縁は、放っといて自然に戻る縁やない。どっちかが動かん限り、このまま風化していく縁や。"),

    ("honne", "彼の今の本音", 2100,
     "この鑑定書の核。未来の問い——『彼から動くタイミングはあるか』『彼は今どう思とるか』——に答える。"
     "ただし今回は、**もう一つの問いにも同時に答える**：『なんで自分は一年経っても終われへんのか』。"
     "まず彼の側から。事実を積み上げる。"
     "①別れの言い方が『一旦別れよう』やった。完全に切る言葉やない。"
     "②別れたあとも会ってご飯を食べとった。切りたい相手とは飯を食わん。"
     "③離れ方が、はっきり言わずにフェードアウトやった。宣言できんかったということや。"
     "④金を借り続けとった。返しながら、また借りる。繋がり続ける口実としても機能しとった。"
     "⑤断られた返事が『わかった』の四文字。そして即ブロック。"
     "この五つから読むと、彼の中で未来はまだ『終わった人』になっとらん。せやけど——ここからが厳しい話や。"
     "**彼の中に気持ちが残っとることと、彼が動くことは、この男の場合ほぼ別の話や。**"
     "この男は、自分が情けない状態のときには絶対に出てこられん。金を借りて、断られて、逃げた。今その記憶の中におる。"
     "『彼から動くタイミングはあるか』——正直に答える。**今のままでは、来ん可能性のほうが高い。**"
     "理由は二つ。ブロックという物理的な壁を自分で作ってしもたこと。そして、この男が戻るには『胸を張れる自分』が要ること。"
     "ただし断定はせん。彼が仕事や生活で自信を取り戻した時、ふっと連絡してくる可能性は残っとる。"
     "そこで、もう一つの問いに移る。未来のモヤモヤの正体や。"
     "未来が引っかかっとるんは『話し合いの内容』と『別れた後の言動』やと自分で書いとる。"
     "これはつまり、**説明されんまま終わったこと**への引っかかりや。なんで別れることになったんか、なんで離れていったんか、なんでブロックしたんか。"
     "一つも説明されとらん。あんたが忘れられへんのは、彼が好きやからやのうて、**話が途中で切れとるから**や。"
     "人の心は、途中で切れた話を勝手に反芻する。それが一年続いとる正体や、と言い切る。"
     "最後に、未来を安心させる一文：あんたが変やない。一年引きずるんが異常なんやない。終わってへん話を、心が終わらせられんだけや。"),

    ("shohousen", "いつ、何を、どう動くか", 2400,
     "処方箋の章。今日は2026年8月2日。別れて一年、連絡が途切れて一ヶ月以上、LINEもインスタもブロックされとる。"
     "まず大前提を置く。未来が求めとるんは復縁やない。**納得**や。せやからこの章は『納得を取りに行く道』として書く。"
     "そのうえで、二つの道を最後まで書き切る。どっちを選んでもええ、と明示する。"
     "【道A：向き合いにいく】"
     "納得するには、彼の口から説明を聞くしかない。それが未来の欲しいものやから。"
     "ただしブロックされとる今、正面からは届かん。使える経路を順に評価する："
     "・電話番号（生きとるか不明。いきなり電話はこの男を固める。最終手段）"
     "・X（アカウントは知っとる。ただしSNSで接触するんは、また同じ土俵に戻るだけ）"
     "・共通の友達（一番現実的やが、外堀から囲む形になるので使い方を誤ると逆効果）"
     "・生活圏（実家暮らしなら近い。ただし待ち伏せのような形は絶対にあかん）"
     "そのうえで、椿の推奨を一つに絞る。**共通の友達を『伝言役』やのうて『確認役』として一度だけ使う。**"
     "『元気にしてるか、それだけ知りたい』の一点に絞る。復縁の意思も、責める気配も一切乗せない。"
     "理由も書く：この男は追い詰められると固まる。外堀を埋められたと感じた瞬間に、二度と出てこん。"
     "時期の目安：今すぐは動かん。ブロックから一ヶ月ちょっとでは、彼の中の気まずさがまだ生々しい。"
     "秋口——十月前後、彼の生活が落ち着いて、断られた記憶が薄れる頃を一つの目安として渡す（断定はせず『そのあたり』の書き方で）。"
     "もし彼のほうから連絡が来た場合の受け方も具体的に書く。"
     "この男は『元気？』みたいな軽い一言で戻ってこようとする可能性が高い。そこで感情をぶつけたら、また逃げる。"
     "聞きたいことを聞く順番と、実際の文面を未来自身の言葉で一つ書く（椿の関西弁は混ぜない）。"
     "責める形にせず、『あの時どう思ってたか、それだけ知りたい』の一点に絞った短い言葉にする。"
     "【道B：納得して終わらせる】"
     "こっちも同じ熱量で書く。逃げの選択肢として書かん。"
     "彼から説明が得られん場合、あるいは向き合ってみて『やっぱり無理や』と分かった場合。"
     "その時どうやって終わらせるか。**説明が得られんかったという事実が、それ自体が答えになる**、という視点を渡す。"
     "八年を否定せずに終わる方法を書く。あの八年が無駄やったわけやない、という締め方を具体的に。"
     "そして最後に一番大事なことを書く：どっちの道を選んでも、**先に必要なんは同じ一つのこと**や。"
     "それは『あんたが何を知りたいのか』を、自分の言葉で一行にすること。それが決まらんうちは、どっちにも進めん。"
     "『この通りやれば必ず戻れる』とは書かない。"),

    ("kinki", "やったらあかんこと", 900,
     "この状況でやってはいけないことを具体的に。"
     "①ブロックを何度も確認しにいくこと。確認するたびに、あんたの中の傷が新しくなるだけや。事実はもう分かっとる。"
     "②共通の友達に、彼の近況を繰り返し探らせること。一度だけならええ。繰り返したら伝わる。この男は人の目を気にする。"
     "③XやSNSで彼を追いかけること。見たところで納得は手に入らん。むしろモヤモヤが増えるだけや。"
     "④感情が高ぶった夜に、電話番号に連絡すること。この男は不意打ちに一番弱い。固まって終わる。"
     "⑤金の話を蒸し返すこと。返済は全部済んどる。もう終わった話や。持ち出したら、それが最後の会話になる。"
     "⑥『復縁したいのかどうか、はっきりさせなあかん』と自分を追い込むこと。決まってへんのは自然や。急いで結論を出す必要はない。"
     "⑦一年も引きずっとる自分を責めること。八年の話が途中で切れたんやから、一年で終わらんのは当たり前や。"
     "すでにできていることを具体的に褒めて、続けさせる："
     "『お金は貸せない』とはっきり断れたこと。八年一緒におった相手に、これを言えるんは簡単やない。"
     "ブロックされてからこの一年、追いかけずに自分の生活を保ってきたこと。それも力や。"),

    ("musubi", "むすびに", 800,
     "締めの章。未来の問いに、最後にもう一度短く答える。"
     "彼の中で終わっとらんのは事実。せやけど彼が動くかは別。そして——**あんたが終われてへんのは、彼のせいやのうて、話が途中で切れとるからや。**"
     "ここを取り違えたらあかん。あんたに必要なんは、彼の気持ちやのうて、**説明**や。"
     "そのうえで、この鑑定書で一番伝えたいことを渡す。"
     "高校三年から八年。十代の記憶も、二十代前半の記憶も、全部そこに彼が写っとる。それを消せんのは当たり前や。"
     "せやけどな、未来。あんたはもう二十七や。**あの八年は、彼のものやのうて、あんたのもんや。**"
     "戻っても、終わっても、あの八年があんたを作ったことは変わらん。そこは誰にも取られへん。"
     "『お互い向き合えないなら終わりでスッキリする、気が済む』——あんたはもう答えを半分持っとる。"
     "この鑑定書は、その半分を全部にするための地図や、と伝える。"
     "困ったらまた椿に相談できること（何度でも相談できる月額の会員があること）にひとことだけ触れ、"
     "最後は椿らしい愛のある一言で結ぶ。締めの一文に🌙を1つ。"),
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
    """HTMLをPDFに変換する。

    ★2026-08-02: 相対パスを渡すと Chrome が file://<相対パス> を解決できず、
    「このサイトにアクセスできません」というエラー画面をそのままPDF化してしまう
    （生成は成功扱いになるので気づけない）。必ず絶対パスに直してから渡す。
    """
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    if not html_path.exists():
        raise FileNotFoundError(f"HTMLが見つかりません: {html_path}")
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf_path}", html_path.as_uri()],
        check=True, capture_output=True, timeout=120,
    )
    # 変換に失敗するとエラー画面がPDF化される。中身を見て検知する
    try:
        from pypdf import PdfReader
        head = (PdfReader(str(pdf_path)).pages[0].extract_text() or "")[:200]
        if "アクセスできません" in head or "ERR_" in head or "not be reached" in head:
            raise RuntimeError(f"PDFにエラー画面が入りました。HTMLのパスを確認してください: {html_path}")
    except ImportError:
        pass


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
