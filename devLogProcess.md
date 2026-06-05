torrented seinfeld all 9 seasons
use python scripts to
renamed episodes to be s##e##
extracted audio from each episode as a .wav file
extracted subtitle text from each episode as a .srt file
<img src="devlogPics/srtExample.png" alt="" style="display: inline-block; height: 1.25rem; width: auto; vertical-align: text-bottom; margin: 0 0.25rem;" />
then from each srt file only the text was extracted and placed into a txt file for each episode, along with removing text which was not spoken words
such as [audience laughter] and a bunch of other stuff enclosed in square brackets

set up montreal forced aligner docker image
https://montreal-forced-aligner.readthedocs.io/en/latest/first_steps/index.html

after installing docker and getting the montreal forced aligner docker image
https://montreal-forced-aligner.readthedocs.io/en/latest/installation.html


docker run -it -v D:\Jesse\Documents\github\mfaDocker:/data mmcauliffe/montreal-forced-aligner:latest
this creates the docker with this directory as the folder "data" in the docker.
from there I can run mfa commands

I kept a file with all the commands I was running because I would realize something was wrong and have to restart a few times

#download models:
mfa model download acoustic english_mfa

mfa model download dictionary english_us_mfa

#this one is for words that aren't in the english dictionary, generates pronounciations for them
mfa model download g2p english_us_arpa


#original command attempt
mfa align /data english_us_mfa english_mfa /data/output --output_format json

this originally kind of failed and I realized the 23 minute wavs with the blocks of text weren't working that well.
thankfully mfa can also use textgrids, which I could use the subtitle timing info for
they look like this
<img src="devlogPics/textgridExample.png" alt="" style="display: inline-block; height: 1.25rem; width: auto; vertical-align: text-bottom; margin: 0 0.25rem;" />

#then went on to mess around with the mfa docker more 
#honestly still not 100% sure what validate does, but it did output some interesting information about words that weren't compatible for being aligned and led me to start using 
#the g2p english_us_arpa model to help
mfa validate /data/Seinfeld/Season1 english_us_mfa english_mfa --output_directory /data/output --output_format json --g2p_model_path english_us_arpa --clean
around this point I realized from one of the output files from validate that a bunch of words weren't working since they werent in the english_us_mfa dictionary, this included words
with dashes but also included numbers which were given as actual numbers like 25 not twenty five and therefore didnt work with the model
<img src="devlogPics/wordsNotFound.png" alt="" style="display: inline-block; height: 1.25rem; width: auto; vertical-align: text-bottom; margin: 0 0.25rem;" />
thankfully there is a numbers to words python library that I was able to use to help with this, didnt get everything but helped a lot.

also discovered in the mfa docs you can fine tune the model on your set of words

mfa adapt /data/Seinfeld/allWavAndTG english_us_mfa english_mfa /data/models/seinfeld_adapted.zip --output_directory /data/output_adapted --output_format json --fine_tune --clean

and then finally used this command to run on all episode .wav and .textgrid files, i dont have the terminal session from this run open anymore but below ill show a demo run on 4 episodes and the output
mfa align /data/Seinfeld/allWavAndTG english_us_mfa /data/models/seinfeld_adapted.zip /data/output_final --output_format json --g2p_model_path english_us_arpa --fine_tune --clean

<img src="devlogPics/mfaAlignDemoRun.png" alt="" style="display: inline-block; height: 1.25rem; width: auto; vertical-align: text-bottom; margin: 0 0.25rem;" />

the seinfeldDemo folder contained these files
<img src="devlogPics/seinfeldDemo.png" alt="" style="display: inline-block; height: 1.25rem; width: auto; vertical-align: text-bottom; margin: 0 0.25rem;" />

and then a json file like this for every .wav and .textgrid I had (one for each episode)
<img src="devlogPics/mfaDockerAlignementOutput.png" alt="" style="display: inline-block; height: 1.25rem; width: auto; vertical-align: text-bottom; margin: 0 0.25rem;" />


this last mfa align command also output a alignment_analysis.csv file which by blank or populated rows showed me how many words had been successfully aligned acorss the whole show,
apparently around 94 percent

I then started working on python program which would go though all the alignment info json files and get all the aligned words available. Then a simple ui
with a text input bar that would show/autocomplete available words below as you typed, and hitting enter would add them to a list of words, from which you could 
build a video with those words in order, thankfully there ffmpeg has a python api to help with this! At this point I realized although 94% were "successfully aligned" a lot of words, especially ones that are spoken quickly 
and fall in the middle of sentences dont cut out well, there were also many cases where alignment wasn't perfect

-word 2 vec word quality scoring

-n gram implementation to be able to build sentences with longer phrases 1-5 words

-realizing for a web build loading an episode is too big on browser, huge internet usage
-splitting into indiviudal lines and updating n gram information to reflect this, since timings are no longer from the start of the episode but need to be calculated 
from the start of the line file
-upload all lines to digital ocean space and put the web build on my digital ocean droplet
-test lots :')
-add a funky theme
-send shitpost vids to ppl