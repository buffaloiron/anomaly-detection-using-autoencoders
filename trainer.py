import math
import random
from ignite.engine import Engine, Events
from ignite.metrics import Loss, RunningAverage
from ignite.metrics import ConfusionMatrix
from tensorboardX import SummaryWriter
#from torch.utils.tensorboard import SummaryWriter
import pathlib
from torchvision import datasets, transforms
import torch
from ignite.handlers import Checkpoint, DiskSaver, global_step_from_engine
import os
import torchvision.utils as vutils


def create_summary_writer(model, train_loader, log_dir, save_graph, device):
    """Creates a tensorboard summary writer

    Arguments:
        model {pytorch model}     -- the model whose graph needs to be saved
        train_loader {dataloader} -- the training dataloader
        log_dir {str}             -- the logging directory path
        save_graph {bool}         -- if True a graph is saved into the
                                     tensorboard log folder
        device {torch.device}     -- torch device object

    Returns:
        writer -- tensorboard SummaryWriter object
    """
    writer = SummaryWriter(log_dir=log_dir)
    if save_graph:
        images, labels = next(iter(train_loader))
        images = images.to(device)
        print(images.shape)
        try:
            writer.add_graph(model, images)
        except Exception as e:
            print("Failed to save model graph: {}".format(e))
    return writer

def random_erasing(img,probability = 1, sl = 0.001, sh = 0.01, r1 = 0.3,mean=1):
    '''
    img:Bx1xHxW

    '''
    if random.uniform(0, 1) > probability:
        return img
    for batch in range(img.size()[0]):#process each batch
        for attempt in range(100):
            area = img.size()[2] * img.size()[3]
            target_area = random.uniform(sl, sh) * area
            aspect_ratio = random.uniform(r1, 1/r1)
            h = int(round(math.sqrt(target_area * aspect_ratio)))
            w = int(round(math.sqrt(target_area / aspect_ratio)))

            if w < img.size()[3] and h < img.size()[2]:
                x1 = random.randint(0, img.size()[2] - h)
                y1 = random.randint(0, img.size()[3] - w)
                img[batch,0, x1:x1+h, y1:y1+w] = mean
                break
    return img


def train(model, optimizer, loss_fn, train_loader, val_loader,
          log_dir, device, epochs, log_interval,
          load_weight_path=None, save_graph=True):
    """Training logic for the wavelet model

    Arguments:
        model {pytorch model}       -- the model to be trained
        optimizer {torch optim}     -- optimiser to be used
        loss_fn                        -- loss_fn function
        train_loader {dataloader}   -- training dataloader
        val_loader {dataloader}     -- validation dataloader
        log_dir {str}               -- the log directory
        device {torch.device}       -- the device to be used e.g. cpu or cuda
        epochs {int}                -- the number of epochs
        log_interval {int}          -- the log interval for train batch loss

    Keyword Arguments:
        load_weight_path {str} -- Model weight path to be loaded (default: {None})
        save_graph {bool}      -- whether to save the model graph (default: {False})

    Returns:
        None
    """
    model.to(device)
    if load_weight_path is not None:
        model.load_state_dict(torch.load(load_weight_path))

    optimizer = optimizer(model.parameters())
    
    def process_function(engine, batch):
        model.train()
        optimizer.zero_grad()
        x, _ = batch
        x = x.to(device)
        x_hat= random_erasing(x.clone(),mean=random.uniform(0,1))
        x_hat = x_hat.to(device)
        #add random noise
        #shape=x.size()
        #noise = torch.cuda.FloatTensor(shape) if torch.cuda.is_available() else torch.FloatTensor(shape)
        #torch.randn(shape, out=noise)
        y = model(x_hat)
        ''' 
        writer = SummaryWriter(log_dir='Result/rnd_erasing')
        
        img_grid = vutils.make_grid(y[0][0], normalize=True, scale_each=True, nrow=4) 
        writer.add_image(f'predicted'+str(random.randint(0,10)), img_grid, global_step=random.randint(0,10))
        img_grid = vutils.make_grid(x[0][0], normalize=True, scale_each=True, nrow=4) 
        writer.add_image(f'x'+str(random.randint(0,10)), img_grid, global_step=random.randint(0,10))
        
        writer.close()
        '''
        loss = loss_fn(y, x)
        loss.backward()
        optimizer.step()
        

        return loss.item()

    def evaluate_function(engine, batch):
        model.eval()
        with torch.no_grad():
            x, _ = batch
            x = x.to(device)
            y = model(x)
            loss = loss_fn(y,x)
            return loss.item()

    trainer = Engine(process_function)
    evaluator = Engine(evaluate_function)

    RunningAverage(output_transform=lambda x:x).attach(trainer,'loss')
    RunningAverage(output_transform=lambda x:x).attach(evaluator,'loss')


    writer = create_summary_writer(model, train_loader, log_dir,
                                   save_graph, device)
    def score_function(engine):
        return -engine.state.metrics['loss']

    to_save = {'model': model}
    handler = Checkpoint(
        to_save,
        DiskSaver(os.path.join(log_dir, 'models'), create_dir=True,require_empty=False),
        n_saved=5, filename_prefix='best', score_function=score_function,
        score_name="loss",
        global_step_transform=global_step_from_engine(trainer))

    evaluator.add_event_handler(Events.COMPLETED, handler)

    @trainer.on(Events.ITERATION_COMPLETED(every=log_interval))
    def log_training_loss(engine):
        print(
            f"Epoch[{engine.state.epoch}] Iteration[{engine.state.iteration}/"
            f"{len(train_loader)}] Loss: {engine.state.output:.3f}"
        )
        writer.add_scalar("training/loss", engine.state.output,
                          engine.state.iteration)

    @trainer.on(Events.EPOCH_COMPLETED)
    def log_training_results(engine):
        evaluator.run(train_loader)
        metrics = evaluator.state.metrics
        avg_loss = metrics["loss"]
        print(
            f"Training Results - Epoch: {engine.state.epoch} Avg loss: {avg_loss:.3f}"
        )
        writer.add_scalar("training/avg_loss", avg_loss, engine.state.epoch)

    @trainer.on(Events.EPOCH_COMPLETED)
    def log_validation_results(engine):
        evaluator.run(val_loader)
        metrics = evaluator.state.metrics
        avg_loss = metrics["loss"]

        print(
            f"Validation Results - Epoch: {engine.state.epoch} Avg loss: {avg_loss:.3f}"
        )
        writer.add_scalar("validation/avg_loss", avg_loss, engine.state.epoch)

    trainer.run(train_loader, max_epochs=epochs)

    writer.close()
