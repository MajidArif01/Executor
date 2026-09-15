import base64
import io
import json
import time
from pathlib import Path
from typing import List, Union

import cv2
import easyocr
import numpy as np
import supervision as sv
import torch
from paddleocr import PaddleOCR
from PIL import Image
from torchvision.ops import box_convert
from torchvision.transforms import ToPILImage

from .box_annotator import BoxAnnotator

_easyocr_reader = None
_paddle_ocr = None
_rapid_ocr = {}

# RapidOCR filters by *recognition* confidence, unlike EasyOCR's `text_threshold`
# (a detection heatmap cutoff). Forwarding EasyOCR's 0.9 here would drop most
# valid UI text, so the rapidocr path uses its own threshold.
#
# These are only the fallback defaults for callers that pass no params of their
# own; parse.py drives the engine from its RAPIDOCR_PARAMS config block.
# Deliberately no Det/Rec.lang_type here: at the default PP-OCRv6 it is
# validated but ignored (the multilingual model is always used), so setting it
# would only mislead. It selects a real per-language model at v4/v5 only.
RAPIDOCR_TEXT_SCORE = 0.5
RAPIDOCR_BOX_THRESH = 0.3
RAPIDOCR_DEFAULT_PARAMS = {
    'Global.text_score': RAPIDOCR_TEXT_SCORE,
    'Det.box_thresh': RAPIDOCR_BOX_THRESH,
}


def get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None:
        _easyocr_reader = easyocr.Reader(['en'])
    return _easyocr_reader


def get_paddle_ocr():
    global _paddle_ocr
    if _paddle_ocr is None:
        _paddle_ocr = PaddleOCR(
            lang='en',
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _paddle_ocr


def _coerce_rapid_ocr_params(params):
    """Turn plain-string params into the Enums RapidOCR demands.

    RapidOCR rejects strings for engine_type / model_type / ocr_version /
    task_type (parse_parameters.ParseParams.update_batch), but config files
    are far more readable with strings, so callers write e.g.
    "Rec.model_type": "small" and the conversion happens here. Values that
    are already Enums pass through untouched.
    """
    from enum import Enum

    from rapidocr.utils.typings import EngineType, ModelType, OCRVersion, TaskType

    enum_types = {
        'engine_type': EngineType,
        'model_type': ModelType,
        'ocr_version': OCRVersion,
        'task_type': TaskType,
    }

    coerced = {}
    for key, value in params.items():
        enum_type = enum_types.get(key.split('.')[-1])
        if enum_type is not None and not isinstance(value, Enum):
            try:
                value = enum_type(value)
            except ValueError:
                valid = [m.value for m in enum_type]
                raise ValueError(
                    f'Invalid RapidOCR {key}={value!r}; expected one of {valid}'
                ) from None
        coerced[key] = value
    return coerced


def get_rapid_ocr(params=None):
    """Build (and cache) a RapidOCR engine for one parameter set.

    ``params`` keys go straight to ``RapidOCR(params=...)``, so anything in
    rapidocr's config.yaml is settable -- including model selection via
    ``Det/Rec.ocr_version`` + ``model_type`` (+ ``lang_type`` at v4/v5).
    Each distinct parameter set gets its own cached engine, so changing the
    model never hands back an engine built for the previous one.
    """
    # Imported lazily (unlike easyocr/paddleocr above) so that a missing
    # rapidocr install cannot break this whole module.
    from rapidocr import RapidOCR

    merged = _coerce_rapid_ocr_params(
        {**RAPIDOCR_DEFAULT_PARAMS, **(params or {})}
    )
    key = json.dumps(merged, sort_keys=True, default=str)
    if key not in _rapid_ocr:
        _rapid_ocr[key] = RapidOCR(params=merged)
    return _rapid_ocr[key]


def _parse_rapid_ocr_result(result, text_threshold):
    coord = []
    text = []

    # RapidOCR returns None when it detects nothing (EasyOCR returns []).
    if result is None or result.boxes is None or len(result.boxes) == 0:
        return coord, text

    scores = result.scores if result.scores is not None else [1.0] * len(result.boxes)
    for poly, txt, score in zip(result.boxes, result.txts, scores):
        if score > text_threshold:
            if hasattr(poly, 'tolist'):
                poly = poly.tolist()
            coord.append(poly)
            text.append(txt)

    return coord, text


def _parse_paddle_ocr_result(result, text_threshold):
    coord = []
    text = []

    if not result:
        return coord, text

    first = result[0]
    if isinstance(first, (list, tuple)) and first and isinstance(first[0], (list, tuple, np.ndarray)):
        # Legacy PaddleOCR output: [[box, (text, score)], ...]
        for item in first:
            if len(item) < 2:
                continue
            score = item[1][1] if isinstance(item[1], (list, tuple)) and len(item[1]) > 1 else 1.0
            if score > text_threshold:
                coord.append(item[0])
                text.append(item[1][0] if isinstance(item[1], (list, tuple)) else item[1])
        return coord, text

    for page in result:
        page_data = dict(page)
        polys = page_data.get('rec_polys') or page_data.get('dt_polys') or []
        texts = page_data.get('rec_texts') or []
        scores = page_data.get('rec_scores') or [1.0] * len(texts)
        for poly, txt, score in zip(polys, texts, scores):
            if score > text_threshold:
                if hasattr(poly, 'tolist'):
                    poly = poly.tolist()
                coord.append(poly)
                text.append(txt[0] if isinstance(txt, (list, tuple)) else txt)

    return coord, text


def get_caption_model_processor(model_name="florence2", model_name_or_path=None, device=None):
    from transformers import AutoProcessor, AutoModelForCausalLM

    if model_name_or_path is None:
        model_name_or_path = str(Path(__file__).resolve().parents[1] / "weights/icon_caption_florence")
    if not device:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float32 if device == 'cpu' else torch.float16
    processor = AutoProcessor.from_pretrained("microsoft/Florence-2-base", trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(model_name_or_path, torch_dtype=dtype, trust_remote_code=True)
    return {'model': model.to(device), 'processor': processor}


def get_yolo_model(model_path=None, device=None):
    from .yolov9 import YOLOv9Detector

    if model_path is None:
        local_model_path = Path(__file__).resolve().parents[1] / "weights/icon_detect_v3/model.pt"
        if local_model_path.is_file():
            model_path = local_model_path

    return YOLOv9Detector(model_path=model_path, device=device)


# Context margin added around each icon box before captioning, as a fraction of the
# box's own width/height. A tight crop gives Florence a glyph with no surroundings;
# 0.25 measurably improves labels ("Uniformiformiform." -> "Pin", "square" -> "Copy").
CAPTION_PAD_FRAC = 0.25

# Side length of the square fed to the caption model. Do NOT raise this to Florence's
# native 768: icon_caption_florence is finetuned on 64px crops, and at 768 it falls back
# to generic base-Florence prose ("A simple symbol or logo.") or "unanswerable".
CAPTION_CROP_SIZE = 64


# Minimum probability the caption model must assign to its WEAKEST token before we
# keep the caption. Below this the label is emitted as "" (unknown) rather than a
# guess -- click_content.py:48 has_no_content() then re-queues that click for a
# context-aware pass, so abstaining is a handoff, not a dead end.
#
# Calibrated on 20 hand-labelled icons from one screenshot: correct captions scored
# 0.096-0.570, wrong ones 0.016-0.316. 0.08 keeps all 6 correct and drops 12 of 14
# wrong. Small sample -- re-tune with the content_confidence values recorded on each
# element if it proves too strict or too loose.
CAPTION_MIN_CONFIDENCE = 0.08

# Florence's literal non-answers, lowercased and stripped of trailing punctuation.
CAPTION_NON_ANSWERS = frozenset({'unanswerable', 'unknown', 'unknow', 'n/a', 'none'})


def _is_degenerate_caption(text):
    """True for captions that are malformed regardless of how confident the model is.

    Repetition loops ("Uniformiformiform.", "Firefoxfox", "Toggleoggleoggle") often
    score HIGH -- the model is very sure about repeating itself -- so confidence
    alone will not catch them.
    """
    cleaned = text.strip().strip('.').lower()
    if not cleaned or cleaned in CAPTION_NON_ANSWERS:
        return True
    # A tail that is some substring repeated back-to-back: "fox"+"fox",
    # "iform"*2 in "Uniformiformiform". Only sizeable units, to avoid firing on
    # ordinary doubled letters ("ll" in "full") or real words like "bookkeeper".
    for unit in range(3, len(cleaned) // 2 + 1):
        chunk = cleaned[-unit:]
        if cleaned[-2 * unit:-unit] == chunk:
            return True
    return False


def _sequence_confidence(scores, sequences, row, eos_id, pad_id):
    """Weakest per-token probability in one generated caption.

    The minimum beats the mean here: a caption is wrong as soon as one token is a
    guess, and averaging lets a confident prefix hide it.

    Skips the forced BOS (shares pad's id, hence the pad test) and EOS -- EOS scores
    ~0.002 almost everywhere, which says nothing about the label and penalises short
    captions like "Settings" far more than long ones.
    """
    import torch as _torch

    worst = None
    for step, step_logits in enumerate(scores):
        token = sequences[row, step + 1].item()
        if token == pad_id:
            continue
        if token == eos_id:
            break
        logprob = _torch.log_softmax(step_logits[row].float(), dim=-1)[token].item()
        prob = float(_torch.tensor(logprob).exp())
        worst = prob if worst is None else min(worst, prob)
    return 0.0 if worst is None else worst


def _pad_box(coord, width, height, pad_frac):
    """Normalized xyxy -> padded pixel xyxy, clamped to the image."""
    # coord entries are torch scalars when filtered_boxes is a tensor; float() them
    # so the arithmetic and round() below are plain Python.
    xmin, ymin = float(coord[0]) * width, float(coord[1]) * height
    xmax, ymax = float(coord[2]) * width, float(coord[3]) * height
    px, py = (xmax - xmin) * pad_frac, (ymax - ymin) * pad_frac
    return (
        max(0, int(xmin - px)),
        max(0, int(ymin - py)),
        min(width, int(round(xmax + px))),
        min(height, int(round(ymax + py))),
    )


def _letterbox_icon(crop, size):
    """Scale ``crop`` to fit ``size``x``size`` keeping aspect, centred on white.

    The original code squashed every crop with ``cv2.resize(crop, (64, 64))``, so a
    145x70 toolbar button was stretched to a square before captioning -- the direct
    cause of degenerate captions on wide boxes.
    """
    h, w = crop.shape[:2]
    scale = size / max(h, w)
    new_h, new_w = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    resized = cv2.resize(crop, (new_w, new_h), interpolation=interp)
    canvas = np.full((size, size, 3), 255, dtype=np.uint8)
    top, left = (size - new_h) // 2, (size - new_w) // 2
    canvas[top:top + new_h, left:left + new_w] = resized
    return canvas


@torch.inference_mode()
def get_parsed_content_icon(filtered_boxes, starting_idx, image_source, caption_model_processor, prompt=None, batch_size=128,
                            pad_frac=CAPTION_PAD_FRAC, crop_size=CAPTION_CROP_SIZE,
                            min_confidence=CAPTION_MIN_CONFIDENCE):
    # Number of samples per batch, --> 128 roughly takes 4 GB of GPU memory for florence v2 model
    to_pil = ToPILImage()
    # -1 means "no box needs a caption"; plain `if starting_idx` treated that as truthy
    # and captioned the last box for nothing.
    if starting_idx < 0:
        return [], []
    non_ocr_boxes = filtered_boxes[starting_idx:] if starting_idx else filtered_boxes
    height, width = image_source.shape[0], image_source.shape[1]
    croped_pil_image = []
    for i, coord in enumerate(non_ocr_boxes):
        # Callers refill `content` positionally with pop(0), so this list MUST stay the
        # same length as non_ocr_boxes -- skipping a bad crop would shift every later
        # caption onto the wrong element. Emit a blank placeholder instead.
        try:
            xmin, ymin, xmax, ymax = _pad_box(coord, width, height, pad_frac)
            cropped_image = image_source[ymin:ymax, xmin:xmax, :]
            cropped_image = _letterbox_icon(cropped_image, crop_size)
        except Exception as exc:
            print(f'icon crop failed for box {i} ({list(coord)}): {exc}')
            cropped_image = np.full((crop_size, crop_size, 3), 255, dtype=np.uint8)
        croped_pil_image.append(to_pil(cropped_image))

    model, processor = caption_model_processor['model'], caption_model_processor['processor']
    if not prompt:
        if 'florence' in model.config.name_or_path:
            prompt = "<CAPTION>"
        else:
            prompt = "The image shows"

    generated_texts = []
    confidences = []
    device = model.device
    is_florence = 'florence' in model.config.name_or_path
    eos_id = model.config.eos_token_id
    pad_id = model.config.pad_token_id
    for i in range(0, len(croped_pil_image), batch_size):
        start = time.time()
        batch = croped_pil_image[i:i+batch_size]
        t1 = time.time()
        # do_resize=False on BOTH devices. The crops are already at the finetune's
        # native size; letting the processor upscale them to 768 (which is what the
        # CPU branch used to do) makes Florence emit generic prose or "unanswerable",
        # so CPU and CUDA runs of the same screenshot disagreed.
        inputs = processor(images=batch, text=[prompt]*len(batch), return_tensors="pt", do_resize=False)
        # Match the model's real dtype, not an assumption from its device: an fp32
        # model on CUDA used to crash here ("Input type Half, bias type float").
        inputs = inputs.to(device=device, dtype=model.dtype)
        if is_florence:
            # output_scores lets us tell a confident label from a guess; without it
            # every caption looks equally authoritative.
            out = model.generate(input_ids=inputs["input_ids"],pixel_values=inputs["pixel_values"],max_new_tokens=20,num_beams=1, do_sample=False,
                                 output_scores=True, return_dict_in_generate=True)
            generated_ids = out.sequences
            batch_conf = [
                _sequence_confidence(out.scores, generated_ids, row, eos_id, pad_id)
                for row in range(len(batch))
            ]
        else:
            generated_ids = model.generate(**inputs, max_length=100, num_beams=5, no_repeat_ngram_size=2, early_stopping=True, num_return_sequences=1) # temperature=0.01, do_sample=True,
            # No scoring for the (currently dead) BLIP2 path: 1.0 keeps every caption,
            # i.e. the old always-answer behaviour.
            batch_conf = [1.0] * len(batch)
        generated_text = processor.batch_decode(generated_ids, skip_special_tokens=True)
        generated_text = [gen.strip() for gen in generated_text]
        generated_texts.extend(generated_text)
        confidences.extend(batch_conf)

    # Abstain: an unconfident or malformed caption becomes "" rather than a guess.
    for idx, (text, conf) in enumerate(zip(generated_texts, confidences)):
        if conf < min_confidence or _is_degenerate_caption(text):
            generated_texts[idx] = ""

    return generated_texts, confidences




def remove_overlap_new(boxes, iou_threshold, ocr_bbox=None):
    '''
    ocr_bbox format: [{'type': 'text', 'bbox':[x,y], 'interactivity':False, 'content':str }, ...]
    boxes format: [{'type': 'icon', 'bbox':[x,y], 'interactivity':True, 'content':None }, ...]

    '''
    assert ocr_bbox is None or isinstance(ocr_bbox, List)

    def box_area(box):
        return (box[2] - box[0]) * (box[3] - box[1])

    def intersection_area(box1, box2):
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])
        return max(0, x2 - x1) * max(0, y2 - y1)

    def IoU(box1, box2):
        intersection = intersection_area(box1, box2)
        union = box_area(box1) + box_area(box2) - intersection + 1e-6
        if box_area(box1) > 0 and box_area(box2) > 0:
            ratio1 = intersection / box_area(box1)
            ratio2 = intersection / box_area(box2)
        else:
            ratio1, ratio2 = 0, 0
        return max(intersection / union, ratio1, ratio2)

    def is_inside(box1, box2):
        # return box1[0] >= box2[0] and box1[1] >= box2[1] and box1[2] <= box2[2] and box1[3] <= box2[3]
        intersection = intersection_area(box1, box2)
        ratio1 = intersection / box_area(box1)
        return ratio1 > 0.80

    # boxes = boxes.tolist()
    filtered_boxes = []
    if ocr_bbox:
        filtered_boxes.extend(ocr_bbox)
    # print('ocr_bbox!!!', ocr_bbox)
    for i, box1_elem in enumerate(boxes):
        box1 = box1_elem['bbox']
        is_valid_box = True
        for j, box2_elem in enumerate(boxes):
            # keep the smaller box
            box2 = box2_elem['bbox']
            if i != j and IoU(box1, box2) > iou_threshold and box_area(box1) > box_area(box2):
                is_valid_box = False
                break
        if is_valid_box:
            if ocr_bbox:
                # keep yolo boxes + prioritize ocr label
                box_added = False
                ocr_labels = ''
                for box3_elem in ocr_bbox:
                    if not box_added:
                        box3 = box3_elem['bbox']
                        if is_inside(box3, box1): # ocr inside icon
                            # box_added = True
                            # delete the box3_elem from ocr_bbox
                            try:
                                # gather all ocr labels
                                ocr_labels += box3_elem['content'] + ' '
                                filtered_boxes.remove(box3_elem)
                            except:
                                continue
                            # break
                        elif is_inside(box1, box3): # icon inside ocr, don't added this icon box, no need to check other ocr bbox bc no overlap between ocr bbox, icon can only be in one ocr box
                            box_added = True
                            break
                        else:
                            continue
                if not box_added:
                    if ocr_labels:
                        filtered_boxes.append({'type': 'icon', 'bbox': box1_elem['bbox'], 'interactivity': True, 'content': ocr_labels, 'source':'box_yolo_content_ocr'})
                    else:
                        filtered_boxes.append({'type': 'icon', 'bbox': box1_elem['bbox'], 'interactivity': True, 'content': None, 'source':'box_yolo_content_yolo'})
            else:
                filtered_boxes.append(box1)
    return filtered_boxes # torch.tensor(filtered_boxes)


def annotate(image_source: np.ndarray, boxes: torch.Tensor, logits: torch.Tensor, phrases: List[str], text_scale: float,
             text_padding=5, text_thickness=2, thickness=3) -> np.ndarray:
    """
    This function annotates an image with bounding boxes and labels.

    Parameters:
    image_source (np.ndarray): The source image to be annotated.
    boxes (torch.Tensor): A tensor containing bounding box coordinates. in cxcywh format, pixel scale
    logits (torch.Tensor): A tensor containing confidence scores for each bounding box.
    phrases (List[str]): A list of labels for each bounding box.
    text_scale (float): The scale of the text to be displayed. 0.8 for mobile/web, 0.3 for desktop # 0.4 for mind2web

    Returns:
    np.ndarray: The annotated image.
    """
    h, w, _ = image_source.shape
    boxes = boxes * torch.Tensor([w, h, w, h])
    xyxy = box_convert(boxes=boxes, in_fmt="cxcywh", out_fmt="xyxy").numpy()
    xywh = box_convert(boxes=boxes, in_fmt="cxcywh", out_fmt="xywh").numpy()
    detections = sv.Detections(xyxy=xyxy)

    labels = [f"{phrase}" for phrase in range(boxes.shape[0])]

    box_annotator = BoxAnnotator(text_scale=text_scale, text_padding=text_padding,text_thickness=text_thickness,thickness=thickness) # 0.8 for mobile/web, 0.3 for desktop # 0.4 for mind2web
    annotated_frame = image_source.copy()
    annotated_frame = box_annotator.annotate(scene=annotated_frame, detections=detections, labels=labels, image_size=(w,h))

    label_coordinates = {f"{phrase}": v for phrase, v in zip(phrases, xywh)}
    return annotated_frame, label_coordinates


def predict_yolo(model, image, box_threshold, imgsz, scale_img, iou_threshold=0.7):
    """ Use huggingface model to replace the original model
    """
    # model = model['model']
    if scale_img:
        result = model.predict(
        source=image,
        conf=box_threshold,
        imgsz=imgsz,
        iou=iou_threshold, # default 0.7
        )
    else:
        result = model.predict(
        source=image,
        conf=box_threshold,
        iou=iou_threshold, # default 0.7
        )
    boxes = result[0].boxes.xyxy#.tolist() # in pixel space
    conf = result[0].boxes.conf
    phrases = [str(i) for i in range(len(boxes))]

    return boxes, conf, phrases

def int_box_area(box, w, h):
    x1, y1, x2, y2 = box
    int_box = [int(x1*w), int(y1*h), int(x2*w), int(y2*h)]
    area = (int_box[2] - int_box[0]) * (int_box[3] - int_box[1])
    return area

def get_som_labeled_img(image_source: Union[str, Image.Image], model=None, BOX_TRESHOLD=0.01, output_coord_in_ratio=False, ocr_bbox=None, text_scale=0.4, text_padding=5, draw_bbox_config=None, caption_model_processor=None, ocr_text=[], use_local_semantics=True, iou_threshold=0.9,prompt=None, scale_img=False, imgsz=None, batch_size=128):
    """Process either an image path or Image object

    Args:
        image_source: Either a file path (str) or PIL Image object
        ...
    """
    if isinstance(image_source, str):
        image_source = Image.open(image_source)
    image_source = image_source.convert("RGB") # for CLIP
    w, h = image_source.size
    if not imgsz:
        imgsz = (h, w)
    # print('image size:', w, h)
    xyxy, logits, phrases = predict_yolo(model=model, image=image_source, box_threshold=BOX_TRESHOLD, imgsz=imgsz, scale_img=scale_img, iou_threshold=0.1)
    xyxy = xyxy / torch.Tensor([w, h, w, h]).to(xyxy.device)
    image_source = np.asarray(image_source)
    phrases = [str(i) for i in range(len(phrases))]

    # annotate the image with labels
    if ocr_bbox:
        ocr_bbox = torch.tensor(ocr_bbox) / torch.Tensor([w, h, w, h])
        ocr_bbox=ocr_bbox.tolist()
    else:
        print('no ocr bbox!!!')
        ocr_bbox = None

    ocr_bbox_elem = [{'type': 'text', 'bbox':box, 'interactivity':False, 'content':txt, 'source': 'box_ocr_content_ocr'} for box, txt in zip(ocr_bbox, ocr_text) if int_box_area(box, w, h) > 0]
    xyxy_elem = [{'type': 'icon', 'bbox':box, 'interactivity':True, 'content':None} for box in xyxy.tolist() if int_box_area(box, w, h) > 0]
    filtered_boxes = remove_overlap_new(boxes=xyxy_elem, iou_threshold=iou_threshold, ocr_bbox=ocr_bbox_elem)

    # sort the filtered_boxes so that the one with 'content': None is at the end, and get the index of the first 'content': None
    filtered_boxes_elem = sorted(filtered_boxes, key=lambda x: x['content'] is None)
    # get the index of the first 'content': None
    starting_idx = next((i for i, box in enumerate(filtered_boxes_elem) if box['content'] is None), -1)
    filtered_boxes = torch.tensor([box['bbox'] for box in filtered_boxes_elem])
    print('len(filtered_boxes):', len(filtered_boxes), starting_idx)

    # get parsed icon local semantics
    time1 = time.time()
    if use_local_semantics:
        parsed_content_icon, parsed_content_conf = get_parsed_content_icon(filtered_boxes, starting_idx, image_source, caption_model_processor, prompt=prompt, batch_size=batch_size)
        # Captions are consumed positionally below; a length mismatch would silently
        # shift every caption onto the wrong element.
        expected = sum(1 for box in filtered_boxes_elem if box['content'] is None)
        assert len(parsed_content_icon) == expected, (
            f'caption/box mismatch: {len(parsed_content_icon)} captions for {expected} boxes'
        )
        ocr_text = [f"Text Box ID {i}: {txt}" for i, txt in enumerate(ocr_text)]
        icon_start = len(ocr_text)
        parsed_content_icon_ls = []
        # fill the filtered_boxes_elem None content with parsed_content_icon in order
        for i, box in enumerate(filtered_boxes_elem):
            if box['content'] is None:
                box['content'] = parsed_content_icon.pop(0)
                # Kept so the abstention threshold stays tunable after the fact; an
                # emptied caption would otherwise leave no trace of why.
                box['content_confidence'] = round(parsed_content_conf.pop(0), 4)
        for i, txt in enumerate(parsed_content_icon):
            parsed_content_icon_ls.append(f"Icon Box ID {str(i+icon_start)}: {txt}")
        parsed_content_merged = ocr_text + parsed_content_icon_ls
    else:
        ocr_text = [f"Text Box ID {i}: {txt}" for i, txt in enumerate(ocr_text)]
        parsed_content_merged = ocr_text
    print('time to get parsed content:', time.time()-time1)

    filtered_boxes = box_convert(boxes=filtered_boxes, in_fmt="xyxy", out_fmt="cxcywh")

    phrases = [i for i in range(len(filtered_boxes))]

    # draw boxes
    if draw_bbox_config:
        annotated_frame, label_coordinates = annotate(image_source=image_source, boxes=filtered_boxes, logits=logits, phrases=phrases, **draw_bbox_config)
    else:
        annotated_frame, label_coordinates = annotate(image_source=image_source, boxes=filtered_boxes, logits=logits, phrases=phrases, text_scale=text_scale, text_padding=text_padding)

    pil_img = Image.fromarray(annotated_frame)
    buffered = io.BytesIO()
    pil_img.save(buffered, format="PNG")
    encoded_image = base64.b64encode(buffered.getvalue()).decode('ascii')
    if output_coord_in_ratio:
        label_coordinates = {k: [v[0]/w, v[1]/h, v[2]/w, v[3]/h] for k, v in label_coordinates.items()}
        assert w == annotated_frame.shape[1] and h == annotated_frame.shape[0]

    return encoded_image, label_coordinates, filtered_boxes_elem


def get_xywh(input):
    x, y, w, h = input[0][0], input[0][1], input[2][0] - input[0][0], input[2][1] - input[0][1]
    x, y, w, h = int(x), int(y), int(w), int(h)
    return x, y, w, h

def get_xyxy(input):
    x, y, xp, yp = input[0][0], input[0][1], input[2][0], input[2][1]
    x, y, xp, yp = int(x), int(y), int(xp), int(yp)
    return x, y, xp, yp

def _run_easyocr(image_np, easyocr_args):
    result = get_easyocr_reader().readtext(image_np, **(easyocr_args or {}))
    return [item[0] for item in result], [item[1] for item in result]


def check_ocr_box(image_source: Union[str, Image.Image], output_bb_format='xywh', goal_filtering=None, easyocr_args=None, use_paddleocr=False, ocr_engine=None, rapidocr_params=None):
    """Run OCR and return ((texts, boxes), goal_filtering).

    ``ocr_engine`` selects 'easyocr', 'paddleocr' or 'rapidocr'. When it is None
    the legacy ``use_paddleocr`` boolean decides, so existing callers are
    unaffected. Both non-default engines fall back to EasyOCR on failure.

    ``rapidocr_params`` tunes the rapidocr engine (thresholds and which model
    to load); see ``get_rapid_ocr``. parse.py passes its RAPIDOCR_PARAMS here.
    """
    engine = ocr_engine or ('paddleocr' if use_paddleocr else 'easyocr')
    if isinstance(image_source, str):
        image_source = Image.open(image_source)
    if image_source.mode == 'RGBA':
        # Convert RGBA to RGB to avoid alpha channel issues
        image_source = image_source.convert('RGB')
    image_np = np.array(image_source)
    w, h = image_source.size
    if engine == 'paddleocr':
        if easyocr_args is None:
            text_threshold = 0.5
        else:
            text_threshold = easyocr_args['text_threshold']
        try:
            result = get_paddle_ocr().predict(image_np, text_rec_score_thresh=text_threshold)
            coord, text = _parse_paddle_ocr_result(result, text_threshold)
        except Exception as exc:
            print(f'PaddleOCR failed ({exc}); falling back to EasyOCR.')
            coord, text = _run_easyocr(image_np, easyocr_args)
    elif engine == 'rapidocr':
        # Filter at the same score the engine was configured with, so a
        # Global.text_score override is not silently overridden here.
        text_threshold = (rapidocr_params or {}).get(
            'Global.text_score', RAPIDOCR_TEXT_SCORE
        )
        try:
            result = get_rapid_ocr(rapidocr_params)(image_np)
            coord, text = _parse_rapid_ocr_result(result, text_threshold)
        except Exception as exc:
            print(f'RapidOCR failed ({exc}); falling back to EasyOCR.')
            coord, text = _run_easyocr(image_np, easyocr_args)
    elif engine == 'easyocr':
        coord, text = _run_easyocr(image_np, easyocr_args)
    else:
        raise ValueError(
            f"Unknown OCR engine {engine!r} (use easyocr, paddleocr or rapidocr)"
        )
    if output_bb_format == 'xywh':
        bb = [get_xywh(item) for item in coord]
    elif output_bb_format == 'xyxy':
        bb = [get_xyxy(item) for item in coord]
    return (text, bb), goal_filtering