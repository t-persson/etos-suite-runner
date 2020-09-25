FROM python:3.8.3

ARG PIP_ARGS

RUN useradd -ms /bin/bash etos
USER etos

ENV PATH="/home/etos/.local/bin:${PATH}"

RUN pip install $PIP_ARGS --upgrade pip
WORKDIR /usr/src/app/src
CMD ["python", "-u", "-m", "etos_suite_runner", "-v"]

COPY requirements.txt /requirements.txt
RUN pip install $PIP_ARGS -r /requirements.txt

COPY . /usr/src/app
